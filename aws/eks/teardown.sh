#!/usr/bin/env bash
# Delete the EKS cluster and the ECR repository created by aws/eks/deploy.sh.
#
#   AWS_REGION=ap-south-1 ./aws/eks/teardown.sh

set -eo pipefail

: "${AWS_REGION:?set AWS_REGION to the region you deployed to}"

eksctl delete cluster --region "${AWS_REGION}" --name chimera --wait
aws ecr delete-repository --region "${AWS_REGION}" --repository-name chimera-controller --force
echo "Deleted. Check the EC2 and EKS consoles for ${AWS_REGION} to confirm nothing is left running."
