#!/usr/bin/env bash
# Host the CHIMERA operator on one small EC2 instance running k3s.
#
# Fits an AWS Free plan account: t3.small is free-tier eligible and usage is drawn from the
# account's credits (roughly 22 USD a month for the instance, a 20 GB disk and a public
# IPv4 address; check current prices). No inbound ports are opened: access is through AWS
# Systems Manager only.
#
# Before running: sign in with `aws login` (or `aws configure`) and set AWS_REGION.
#
#   AWS_REGION=ap-south-1 ./aws/ec2/deploy.sh

set -eo pipefail
export MSYS_NO_PATHCONV=1 AWS_PAGER=""

: "${AWS_REGION:?set AWS_REGION, e.g. AWS_REGION=ap-south-1}"
NAME="chimera-host"
ROLE="chimera-ec2-ssm"
TYPE="${CHIMERA_INSTANCE_TYPE:-t3.small}"
HERE="aws/ec2"   # run from the repository root

existing="$(aws ec2 describe-instances --filters "Name=tag:Name,Values=${NAME}" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query "Reservations[].Instances[].InstanceId" --output text)"
if [ -n "${existing}" ]; then echo "already exists: ${existing}"; exit 0; fi

echo "=== 1. IAM role for Systems Manager access ==="
if ! aws iam get-role --role-name "${ROLE}" >/dev/null 2>&1; then
  aws iam create-role --role-name "${ROLE}" \
    --assume-role-policy-document "file://${HERE}/trust-policy.json" >/dev/null
  aws iam attach-role-policy --role-name "${ROLE}" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  aws iam create-instance-profile --instance-profile-name "${ROLE}" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "${ROLE}" --role-name "${ROLE}"
  sleep 15   # IAM is eventually consistent
fi

echo "=== 2. Security group with no inbound rules ==="
VPC="$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query "Vpcs[0].VpcId" --output text)"
SG="$(aws ec2 describe-security-groups --filters "Name=group-name,Values=${NAME}" "Name=vpc-id,Values=${VPC}" \
  --query "SecurityGroups[0].GroupId" --output text)"
if [ "${SG}" = "None" ] || [ -z "${SG}" ]; then
  SG="$(aws ec2 create-security-group --group-name "${NAME}" --vpc-id "${VPC}" \
    --description "CHIMERA host: outbound only" --query GroupId --output text)"
fi

echo "=== 3. Launch ${TYPE} ==="
AMI="$(aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query Parameter.Value --output text)"
ID="$(aws ec2 run-instances --image-id "${AMI}" --instance-type "${TYPE}" \
  --iam-instance-profile "Name=${ROLE}" --security-group-ids "${SG}" \
  --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=20,VolumeType=gp3,DeleteOnTermination=true}" \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
  --credit-specification "CpuCredits=unlimited" \
  --user-data "file://${HERE}/bootstrap.sh" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${NAME}},{Key=project,Value=chimera}]" \
  --query "Instances[0].InstanceId" --output text)"
echo "instance ${ID}"
aws ec2 wait instance-running --instance-ids "${ID}"

echo "=== 4. Waiting for the first-boot setup (about 10 minutes) ==="
until [ "$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=${ID}" \
  --query "InstanceInformationList[0].PingStatus" --output text)" = "Online" ]; do sleep 10; done
for _ in $(seq 1 90); do
  CMD="$(aws ssm send-command --instance-ids "${ID}" --document-name AWS-RunShellScript \
    --parameters "file://${HERE}/status-command.json" --query Command.CommandId --output text)"
  sleep 15
  OUT="$(aws ssm get-command-invocation --command-id "${CMD}" --instance-id "${ID}" \
    --query StandardOutputContent --output text 2>/dev/null || true)"
  echo "  ${OUT}" | tail -n 1 | cut -c1-150
  case "${OUT}" in *READY*) echo "Deployed on ${ID}."; exit 0 ;; esac
done
echo "Setup did not finish in time; see /var/log/chimera-bootstrap.log on ${ID}."
exit 1
