#!/usr/bin/env bash
# Start, stop or list the AWS self-play workers (tag Project=colonist-ai), from
# a machine with the AWS CLI logged in (aws login --profile colonist).
#   scripts/vm/aws_workers.sh status|start|stop
# A stopped worker costs only its disk; its public IP changes when it starts
# again, so update COLONIST_WORKER_HOSTS in scripts/vm/workers.env on the
# coordinator (the loop rereads it every round; a worker it can't reach just
# has its games played elsewhere).
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-colonist}" AWS_REGION="${AWS_REGION:-us-east-2}"
ids() {
  aws ec2 describe-instances \
    --filters Name=tag:Project,Values=colonist-ai Name=instance-state-name,Values="$1" \
    --query "Reservations[].Instances[].InstanceId" --output text
}
case "${1:-status}" in
  status)
    aws ec2 describe-instances --filters Name=tag:Project,Values=colonist-ai \
      --query "Reservations[].Instances[].[InstanceId,InstanceType,State.Name,PublicIpAddress]" \
      --output table ;;
  start)
    i=$(ids stopped); [ -n "$i" ] && aws ec2 start-instances --instance-ids $i --output text >/dev/null
    [ -n "$i" ] && aws ec2 wait instance-running --instance-ids $i; "$0" status ;;
  stop)
    i=$(ids running); [ -n "$i" ] && aws ec2 stop-instances --instance-ids $i --output text >/dev/null
    "$0" status ;;
  *) echo "usage: $0 status|start|stop" >&2; exit 2 ;;
esac
