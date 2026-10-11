#!/usr/bin/env bash
# Run k8s/live_validation.py on the CHIMERA host through Systems Manager and copy the
# report, timeline and operator log back to results/live_ec2/. Run from the repository root.
#
#   AWS_REGION=ap-south-1 ./aws/ec2/validate.sh

set -eo pipefail
export MSYS_NO_PATHCONV=1 AWS_PAGER=""

: "${AWS_REGION:?set AWS_REGION to the region you deployed to}"
ID="$(aws ec2 describe-instances --filters "Name=tag:Name,Values=chimera-host" \
  "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].InstanceId" --output text)"
[ "${ID}" != "None" ] || { echo "no running chimera-host instance"; exit 1; }

run() {   # run() <parameters file>: send a command, wait for it, print its stdout
  local cmd status
  cmd="$(aws ssm send-command --instance-ids "${ID}" --document-name AWS-RunShellScript \
    --parameters "file://$1" --query Command.CommandId --output text)"
  while :; do
    sleep 10
    status="$(aws ssm get-command-invocation --command-id "${cmd}" --instance-id "${ID}" \
      --query Status --output text 2>/dev/null || echo Pending)"
    case "${status}" in Pending|InProgress|Delayed) ;; *) break ;; esac
  done
  aws ssm get-command-invocation --command-id "${cmd}" --instance-id "${ID}" \
    --query StandardOutputContent --output text
}

echo "=== Validation on ${ID} (about 11 minutes) ==="
run aws/ec2/validate-command.json
echo "=== Fetching results ==="
mkdir -p results
run aws/ec2/fetch-command.json | tr -d '\r\n' | base64 -d | tar xzf - -C results
ls results/live_ec2
