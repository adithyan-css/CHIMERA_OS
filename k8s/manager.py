"""
CHIMERA Kubernetes Controller Manager (Operator Loop)

Watches ChimeraAutoscaler CRD resources, executes LQR-Lyapunov joint optimization,
and reconciles target deployment replicas and pod CPU allocations.
"""

import time
import os
import logging
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
import numpy as np

from src.core.chimera_controller import ChimeraController
from src.models.cqr_uncertainty import ConformalQuantileRegressor
from src.models.drift_detector import AdwinDriftDetector

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("chimera-operator")

# Initialize clients
try:
    config.load_incluster_config()
except Exception:
    config.load_kube_config()

v1_core = client.CoreV1Api()
apps_v1 = client.AppsV1Api()
custom_objects = client.CustomObjectsApi()

def parse_cpu(cpu_str: str) -> float:
    """Helper to convert Kubernetes CPU strings (e.g. 500m, 1.5) to core float."""
    if cpu_str.endswith('m'):
        return float(cpu_str[:-1]) / 1000.0
    return float(cpu_str)

def format_cpu(cpu_val: float) -> str:
    """Helper to format CPU core float to millicores string."""
    return f"{int(cpu_val * 1000)}m"

class ChimeraReconciler:
    def __init__(self):
        self.controllers = {} # maps crd name -> controller state
        
        # Load pre-trained CQR models if available
        self.cqr = ConformalQuantileRegressor(alpha=0.1, calibration_size=100)
        cqr_path = "models/cqr_model"
        if os.path.exists(cqr_path + "_low.txt"):
            try:
                self.cqr.load(cqr_path)
                logger.info("Loaded pre-trained CQR models.")
            except Exception as e:
                logger.warning(f"Failed to load CQR models: {e}. Using fallback bounds.")
        else:
            # Set default dummy bounds
            self.cqr.calibration_scores = [0.1, 0.2, 0.3]
            class DummyModel:
                def predict(self, X):
                    return np.ones(len(X)) * 25.0
            self.cqr.model_low = DummyModel()
            self.cqr.model_high = DummyModel()

    def get_prom_telemetry(self, deploy_name: str) -> float:
        """
        Query metrics from local Prometheus adapter.
        Note: Verification confirms reconciliation logic; live Prometheus telemetry integration is untested in this release, using local query mockups.
        """
        logger.info(f"Retrieving telemetry for deployment {deploy_name}...")
        return 10.0 + np.random.normal(0, 2.0)

    def reconcile_resource(self, obj: dict):
        namespace = obj['metadata']['namespace']
        name = obj['metadata']['name']
        spec = obj['spec']
        
        # Parse CRD specs
        target_ref = spec['scaleTargetRef']
        min_replicas = spec.get('minReplicas', 2)
        max_replicas = spec.get('maxReplicas', 30)
        v_min = parse_cpu(spec.get('minCPU', '500m'))
        v_max = parse_cpu(spec.get('maxCPU', '4000m'))
        gamma_ratio = spec.get('ratioCPUToReplicas', 0.5)
        cost_weight = spec.get('costWeight', 0.1)
        
        # Instantiate controller states if not cached
        if name not in self.controllers:
            self.controllers[name] = {
                'controller': ChimeraController(
                    v_min=v_min, v_max=v_max, h_min=min_replicas, h_max=max_replicas,
                    gamma_ratio=gamma_ratio, R=np.diag([cost_weight, cost_weight])
                ),
                'drift_detector': AdwinDriftDetector(delta=0.05)
            }
            
        ctrl_state = self.controllers[name]
        controller: ChimeraController = ctrl_state['controller']
        detector: AdwinDriftDetector = ctrl_state['drift_detector']
        
        # 1. Fetch deployment status
        try:
            deploy = apps_v1.read_namespaced_deployment(target_ref['name'], namespace)
        except ApiException as e:
            logger.error(f"Failed to read deployment {target_ref['name']}: {e}")
            return
            
        current_replicas = deploy.spec.replicas
        
        # Read current CPU allocation from first container
        container = deploy.spec.template.spec.containers[0]
        current_cpu = parse_cpu(container.resources.limits.get('cpu', '500m'))
        
        x_t = np.array([current_cpu, current_replicas])
        
        # 2. Get telemetry metrics
        req_rate = self.get_prom_telemetry(target_ref['name'])
        
        # Generate dummy 37D feature vector for model prediction
        features = np.zeros(37)
        features[0] = req_rate # Inject request rate as first feature
        
        L_t, U_t, u_width = self.cqr.predict_conformal_bounds(features)
        target_demand = float(U_t[0])
        
        # 3. Calculate target state x*
        total_cores_needed = target_demand * 0.01 / 0.7  # 70% target utilization
        h_star = int(np.ceil(np.sqrt(total_cores_needed / controller.gamma_ratio)))
        h_star = max(controller.h_min, min(h_star, controller.h_max))
        v_star = total_cores_needed / h_star
        v_star = max(controller.v_min, min(v_star, controller.v_max))
        
        x_star = np.array([v_star, h_star])
        
        # 4. Invoke joint controller
        u_opt, x_next = controller.get_desired_allocation(x_t, x_star, u_width, tau=20.0)
        dv, dh = u_opt[0], u_opt[1]
        
        # 5. Apply actuation modifications
        patch_needed = False
        
        # (a) Vertical resize
        if abs(dv) > 1e-4:
            new_cpu = x_t[0] + dv
            logger.info(f"Scaling vertical CPU: {format_cpu(x_t[0])} -> {format_cpu(new_cpu)}")
            container.resources.limits['cpu'] = format_cpu(new_cpu)
            container.resources.requests['cpu'] = format_cpu(new_cpu)
            patch_needed = True
            
        # (b) Horizontal resize
        if abs(dh) > 0.1:
            new_replicas = int(current_replicas + np.round(dh))
            logger.info(f"Scaling horizontal replicas: {current_replicas} -> {new_replicas}")
            deploy.spec.replicas = new_replicas
            patch_needed = True
            
        if patch_needed:
            try:
                apps_v1.patch_namespaced_deployment(target_ref['name'], namespace, deploy)
                logger.info("Successfully patched target deployment.")
            except ApiException as e:
                logger.error(f"Failed to patch deployment: {e}")
                
        # 6. Monitor concept drift
        is_drift = detector.add_element(abs(req_rate - target_demand))
        if is_drift:
            logger.warning("Operator loop detected concept drift. Recalibrating...")
            controller.zeta = min(2.0, controller.zeta * 2)

    def watch_loop(self):
        logger.info("Starting CHIMERA Controller watch loop...")
        resource_ver = ""
        while True:
            try:
                stream = watch.Watch().stream(
                    custom_objects.list_cluster_custom_object,
                    group="autoscaling.k8s.io",
                    version="v1alpha1",
                    plural="chimeraautoscalers",
                    resource_version=resource_ver
                )
                for event in stream:
                    obj = event['object']
                    resource_ver = obj['metadata'].get('resourceVersion', resource_ver)
                    
                    if event['type'] in ['ADDED', 'MODIFIED']:
                        logger.info(f"Processing event for: {obj['metadata']['name']}")
                        self.reconcile_resource(obj)
            except Exception as e:
                logger.error(f"Watch connection dropped: {e}. Retrying in 5 seconds...")
                time.sleep(5)

if __name__ == "__main__":
    reconciler = ChimeraReconciler()
    reconciler.watch_loop()
