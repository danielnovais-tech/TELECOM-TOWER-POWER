#!/usr/bin/env bash
set -euo pipefail

# Run a one-off ECS Fargate task with a custom command.
# Usage:
#   ./scripts/ecs_run_task.sh "alembic upgrade head"
#   ./scripts/ecs_run_task.sh "python migrate_csv_to_db.py --csv towers_brazil.csv"
#   ./scripts/ecs_run_task.sh "python -c 'print(1+1)'"

COMMAND="${1:?Usage: $0 \"<command>\"}"
CLUSTER="telecom-tower-power"
TASK_DEF="telecom-tower-power"
CONTAINER="api"
AWS_REGION="${AWS_REGION:-sa-east-1}"
SUBNETS="subnet-0455f02ba70359087,subnet-01d2842549ded7c71,subnet-07402e9d4181c6a78"
SECURITY_GROUP="sg-0fd5fb2fa66191719"

echo "=== One-off ECS Task ==="
echo "  Command:   $COMMAND"
echo "  Container: $CONTAINER"
echo "  Cluster:   $CLUSTER"
echo ""

# Convert command string to JSON array: "alembic upgrade head" → ["alembic","upgrade","head"]
CMD_JSON=$(echo "$COMMAND" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip().split()))")

TASK_ARN=$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_DEF" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SECURITY_GROUP],assignPublicIp=ENABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"$CONTAINER\",\"command\":$CMD_JSON}]}" \
  --region "$AWS_REGION" \
  --query 'tasks[0].taskArn' \
  --output text)

TASK_ID="${TASK_ARN##*/}"
echo "  Task started: $TASK_ID"
echo "  Waiting for task to complete..."

aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN" --region "$AWS_REGION"

EXIT_CODE=$(aws ecs describe-tasks \
  --cluster "$CLUSTER" \
  --tasks "$TASK_ARN" \
  --region "$AWS_REGION" \
  --query "tasks[0].containers[?name=='$CONTAINER'].exitCode | [0]" \
  --output text)

echo ""
echo "=== Task Logs ==="
aws logs get-log-events \
  --log-group-name "/ecs/$CLUSTER" \
  --log-stream-name "api/$CONTAINER/$TASK_ID" \
  --region "$AWS_REGION" \
  --query 'events[*].message' \
  --output text \
  --no-cli-pager 2>/dev/null || echo "  (no logs found)"

echo ""
if [ "$EXIT_CODE" = "0" ]; then
  echo "✓ Task completed successfully (exit code 0)"
else
  echo "✗ Task failed (exit code $EXIT_CODE)"
  exit 1
fi
