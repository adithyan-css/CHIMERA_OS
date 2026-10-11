#!/usr/bin/env bash
# CHIMERA Kubernetes Operator Quickstart
#
# Creates a local kind cluster on Kubernetes 1.33 (in-place pod resize, KEP-1287, is on
# by default from 1.33), installs a minimal Prometheus, builds and deploys the operator,
# and attaches a ChimeraAutoscaler to a CPU-bound demo service with a load generator.
#
# Requirements: docker, kind (>= 0.27), kubectl.
#
#   ./k8s/quickstart.sh            # full setup
#   ./k8s/quickstart.sh --dry-run  # operator logs decisions but never patches
#   ./k8s/quickstart.sh --delete   # tear the cluster down

set -eo pipefail

CLUSTER_NAME="chimera-cluster"
NODE_IMAGE="kindest/node:v1.33.1"
DRY_RUN="0"

case "${1:-}" in
  --delete) kind delete cluster --name "${CLUSTER_NAME}"; exit 0 ;;
  --dry-run) DRY_RUN="1" ;;
esac

echo "=== 1. Creating kind cluster ${CLUSTER_NAME} (${NODE_IMAGE}) ==="
if ! kind get clusters | grep -q "^${CLUSTER_NAME}$"; then
  kind create cluster --name "${CLUSTER_NAME}" --image "${NODE_IMAGE}"
else
  echo "Cluster ${CLUSTER_NAME} already exists. Skipping creation."
fi

echo "=== 2. Installing minimal Prometheus (cAdvisor metrics) ==="
kubectl apply -f k8s/prometheus.yaml
kubectl -n monitoring rollout status deploy/prometheus --timeout=180s

echo "=== 3. Building the CHIMERA operator image ==="
docker build -t chimera-controller:latest -f k8s/Dockerfile .

echo "=== 4. Loading the image into kind ==="
kind load docker-image chimera-controller:latest --name "${CLUSTER_NAME}"

echo "=== 5. Applying the ChimeraAutoscaler CRD ==="
kubectl apply -f k8s/crd.yaml
kubectl wait --for=condition=Established crd/chimeraautoscalers.autoscaling.chimera.io --timeout=60s

echo "=== 6. Deploying the operator (dry-run=${DRY_RUN}) ==="
kubectl apply -f k8s/controller.yaml
kubectl -n kube-system set env deploy/chimera-controller CHIMERA_DRY_RUN="${DRY_RUN}"
kubectl -n kube-system rollout status deploy/chimera-controller --timeout=180s

echo "=== 7. Deploying a CPU-bound demo service and a load generator ==="
kubectl apply -f k8s/demo-service.yaml

echo "=== 8. Attaching a ChimeraAutoscaler ==="
kubectl apply -f k8s/example-autoscaler.yaml

echo
echo "=== Setup complete ==="
echo "Watch decisions:   kubectl -n kube-system logs -f deploy/chimera-controller"
echo "Watch the scaler:  kubectl get chas -w"
echo "Watch pod CPU:     kubectl get pods -l app=billing-service -o custom-columns=NAME:.metadata.name,CPU:.spec.containers[0].resources.limits.cpu"
echo "Scale load up:     kubectl scale deploy/load-generator --replicas=4"
