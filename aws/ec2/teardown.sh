#!/usr/bin/env bash
# Delete everything aws/ec2/deploy.sh created: the instance (and its disk), the security
# group, and the IAM role and instance profile.
#
#   AWS_REGION=ap-south-1 ./aws/ec2/teardown.sh

set -eo pipefail
export MSYS_NO_PATHCONV=1 AWS_PAGER=""

: "${AWS_REGION:?set AWS_REGION to the region you deployed to}"
NAME="chimera-host"
ROLE="chimera-ec2-ssm"

IDS="$(aws ec2 describe-instances --filters "Name=tag:Name,Values=${NAME}" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query "Reservations[].Instances[].InstanceId" --output text)"
if [ -n "${IDS}" ]; then
  aws ec2 terminate-instances --instance-ids ${IDS} >/dev/null
  aws ec2 wait instance-terminated --instance-ids ${IDS}
fi
SG="$(aws ec2 describe-security-groups --filters "Name=group-name,Values=${NAME}" \
  --query "SecurityGroups[0].GroupId" --output text)"
[ "${SG}" = "None" ] || aws ec2 delete-security-group --group-id "${SG}"
if aws iam get-role --role-name "${ROLE}" >/dev/null 2>&1; then
  aws iam remove-role-from-instance-profile --instance-profile-name "${ROLE}" --role-name "${ROLE}" || true
  aws iam delete-instance-profile --instance-profile-name "${ROLE}" || true
  aws iam detach-role-policy --role-name "${ROLE}" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore || true
  aws iam delete-role --role-name "${ROLE}"
fi
echo "Deleted. Check the EC2 console for ${AWS_REGION} to confirm nothing is left running."
