#!/bin/bash
# First-boot script (EC2 user data) for the single-node CHIMERA host.
# Installs k3s (Kubernetes 1.33, in-place pod resize on by default), builds the operator
# image from the public repository and deploys Prometheus, the operator and the demo
# service. Progress: /var/log/chimera-bootstrap.log. Done when /var/lib/chimera-ready exists.
set -euxo pipefail
exec > /var/log/chimera-bootstrap.log 2>&1
export PATH="/usr/local/bin:$PATH" HOME=/root

REPO="https://github.com/adithyan-css/CHIMERA_OS.git"

# 2 GB of RAM is tight for an image build next to Kubernetes: add swap
dd if=/dev/zero of=/swapfile bs=1M count=2048
chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo "/swapfile none swap sw 0 0" >> /etc/fstab

dnf install -y git docker
systemctl start docker
git clone --depth 1 "${REPO}" /opt/chimera
cd /opt/chimera
docker build -t chimera-controller:latest -f k8s/Dockerfile .

curl -sfL https://get.k3s.io | INSTALL_K3S_CHANNEL=v1.33 sh -s - \
  --disable traefik --disable servicelb --disable metrics-server --write-kubeconfig-mode 644
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
until kubectl get nodes 2>/dev/null | grep -q " Ready"; do sleep 3; done

docker save chimera-controller:latest | k3s ctr images import -
systemctl disable --now docker.socket docker    # only needed for the build; frees memory

kubectl apply -f k8s/prometheus.yaml
kubectl -n monitoring rollout status deploy/prometheus --timeout=300s
kubectl apply -f k8s/crd.yaml
kubectl wait --for=condition=Established crd/chimeraautoscalers.autoscaling.chimera.io --timeout=60s
kubectl apply -f k8s/controller.yaml
kubectl -n kube-system rollout status deploy/chimera-controller --timeout=300s

# The node has 2 vCPUs: start the demo pods at 250m and let CHIMERA scale to 3 x 500m
sed "s/cpu: 500m/cpu: 250m/g" k8s/demo-service.yaml | kubectl apply -f -
cat <<YAML | kubectl apply -f -
apiVersion: autoscaling.chimera.io/v1alpha1
kind: ChimeraAutoscaler
metadata:
  name: billing-service-scaler
  namespace: default
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: billing-service
  minReplicas: 2
  maxReplicas: 3
  minCPU: "250m"
  maxCPU: "500m"
  targetUtilization: 0.7
  podStartupSeconds: 20
  resizeMode: InPlace
YAML
kubectl rollout status deploy/billing-service --timeout=300s
kubectl scale deploy/load-generator --replicas=0

touch /var/lib/chimera-ready
