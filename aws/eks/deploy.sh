#!/usr/bin/env bash
# Deploy the CHIMERA operator to Amazon EKS.
#
# Before running: install the AWS CLI, eksctl, kubectl and Docker; run `aws configure`
# or `aws login` yourself; set AWS_REGION. This creates billable resources (an EKS control
# plane and one t3.xlarge node). Run aws/eks/teardown.sh when you are done.
#
#   AWS_REGION=ap-south-1 ./aws/eks/deploy.sh

set -eo pipefail

: "${AWS_REGION:?set AWS_REGION, e.g. AWS_REGION=ap-south-1}"
CLUSTER="chimera"
REPO="chimera-controller"
TAG="${CHIMERA_TAG:-v5}"

for tool in aws eksctl kubectl docker; do
  command -v "$tool" >/dev/null || { echo "missing: $tool"; exit 1; }
done

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:${TAG}"
echo "Account ${ACCOUNT}, region ${AWS_REGION}, image ${IMAGE}"

echo "=== 1. ECR repository ==="
aws ecr describe-repositories --region "${AWS_REGION}" --repository-names "${REPO}" >/dev/null 2>&1 \
  || aws ecr create-repository --region "${AWS_REGION}" --repository-name "${REPO}" >/dev/null

echo "=== 2. Build and push the operator image ==="
aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${REGISTRY}"
docker build --platform linux/amd64 -t "${IMAGE}" -f k8s/Dockerfile .
docker push "${IMAGE}"

echo "=== 3. EKS cluster (about 15 minutes) ==="
if ! eksctl get cluster --region "${AWS_REGION}" --name "${CLUSTER}" >/dev/null 2>&1; then
  sed "s/REGION/${AWS_REGION}/" aws/eks/cluster.yaml | eksctl create cluster -f -
fi
aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER}"

echo "=== 4. Prometheus, CRD, operator ==="
kubectl apply -f k8s/prometheus.yaml
kubectl -n monitoring rollout status deploy/prometheus --timeout=300s
kubectl apply -f k8s/crd.yaml
kubectl wait --for=condition=Established crd/chimeraautoscalers.autoscaling.chimera.io --timeout=60s
# Same manifest as the kind run, with the ECR image instead of a locally loaded one
sed -e "s#image: chimera-controller:latest#image: ${IMAGE}#" \
    -e "s#imagePullPolicy: Never#imagePullPolicy: IfNotPresent#" k8s/controller.yaml | kubectl apply -f -
kubectl -n kube-system rollout status deploy/chimera-controller --timeout=300s

echo "=== 5. Demo service and autoscaler ==="
kubectl apply -f k8s/demo-service.yaml
kubectl apply -f aws/eks/autoscaler-eks.yaml
kubectl rollout status deploy/billing-service --timeout=300s

echo
echo "Deployed. Validate with:"
echo "  python k8s/live_validation.py --out results/live_eks"
echo "Delete everything with:"
echo "  AWS_REGION=${AWS_REGION} ./aws/eks/teardown.sh"
