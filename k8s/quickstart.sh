#!/usr/bin/env bash
# CHIMERA Kubernetes Operator Quickstart Script
# Sets up a local kind cluster, builds the Docker image, loads it, and deploys the controller.

set -eo pipefail

CLUSTER_NAME="chimera-cluster"

echo "=== 1. Creating kind cluster: ${CLUSTER_NAME} ==="
if ! kind get clusters | grep -q "^${CLUSTER_NAME}$"; then
  kind create cluster --name "${CLUSTER_NAME}"
else
  echo "Cluster ${CLUSTER_NAME} already exists. Skipping creation."
fi

echo "=== 2. Building CHIMERA Operator Docker image ==="
docker build -t chimera-controller:latest -f k8s/Dockerfile .

echo "=== 3. Loading Docker image into kind cluster ==="
kind load docker-image chimera-controller:latest --name "${CLUSTER_NAME}"

echo "=== 4. Applying ChimeraAutoscaler CRD ==="
kubectl apply -f k8s/crd.yaml

echo "=== 5. Deploying CHIMERA Controller Operator ==="
kubectl apply -f k8s/controller.yaml

echo "=== 6. Deploying a mock target application Deployment ==="
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: billing-service
  namespace: default
spec:
  replicas: 2
  selector:
    matchLabels:
      app: billing-service
  template:
    metadata:
      labels:
        app: billing-service
    spec:
      containers:
        - name: web
          image: nginx:alpine
          resources:
            limits:
              cpu: 500m
              memory: 256Mi
            requests:
              cpu: 500m
              memory: 256Mi
EOF

echo "=== 7. Deploying example ChimeraAutoscaler policy ==="
cat <<EOF | kubectl apply -f -
apiVersion: autoscaling.k8s.io/v1alpha1
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
  maxReplicas: 10
  minCPU: "500m"
  maxCPU: "2000m"
  targetSLO: 200
  ratioCPUToReplicas: 0.5
  costWeight: 0.1
EOF

echo "=== Setup complete! Checking pod status... ==="
kubectl get pods -n kube-system -l control-plane=controller-manager
kubectl get chimeraautoscaler billing-service-scaler
