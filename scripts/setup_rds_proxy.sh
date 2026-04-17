#!/usr/bin/env bash
set -euo pipefail
#
# setup_rds_proxy.sh – Create an RDS Proxy for safe Lambda→RDS connectivity.
#
# RDS Proxy pools and shares database connections, preventing connection
# exhaustion when multiple Lambda invocations scale concurrently.
#
# Prerequisites:
#   - AWS CLI configured
#   - Existing RDS instance in a VPC
#   - Secrets Manager secret with RDS credentials (created by this script)
#
# Usage:
#   ./scripts/setup_rds_proxy.sh
#   ./scripts/setup_rds_proxy.sh --rds-instance telecom-tower-power-db
#   ./scripts/setup_rds_proxy.sh --rds-instance telecom-tower-power-db --stage prod
#

STAGE="${STAGE:-prod}"
REGION="${AWS_REGION:-sa-east-1}"
RDS_INSTANCE="${RDS_INSTANCE:-telecom-tower-power-db}"
PROXY_NAME="telecom-tower-power-proxy-${STAGE}"
SECRET_NAME="telecom-tower-power/rds-credentials-${STAGE}"
ROLE_NAME="telecom-tower-power-rds-proxy-role-${STAGE}"
LAMBDA_SG_NAME="telecom-tower-power-lambda-sg-${STAGE}"

# Parse CLI args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)        STAGE="$2"; shift 2 ;;
    --region)       REGION="$2"; shift 2 ;;
    --rds-instance) RDS_INSTANCE="$2"; shift 2 ;;
    --db-user)      DB_USER="$2"; shift 2 ;;
    --db-password)  DB_PASSWORD="$2"; shift 2 ;;
    *)              echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Re-derive names after arg parsing (stage may have changed)
PROXY_NAME="telecom-tower-power-proxy-${STAGE}"
SECRET_NAME="telecom-tower-power/rds-credentials-${STAGE}"
ROLE_NAME="telecom-tower-power-rds-proxy-role-${STAGE}"
LAMBDA_SG_NAME="telecom-tower-power-lambda-sg-${STAGE}"

echo "=========================================="
echo " RDS Proxy Setup"
echo " Stage:    ${STAGE}"
echo " Region:   ${REGION}"
echo " RDS:      ${RDS_INSTANCE}"
echo " Proxy:    ${PROXY_NAME}"
echo "=========================================="

# ── Step 0: Fetch RDS instance info ─────────────────────────────
echo ""
echo "==> Step 0: Fetching RDS instance info..."

RDS_INFO=$(aws rds describe-db-instances \
  --db-instance-identifier "${RDS_INSTANCE}" \
  --region "${REGION}" \
  --output json)

RDS_ARN=$(echo "${RDS_INFO}" | jq -r '.DBInstances[0].DBInstanceArn')
RDS_ENGINE=$(echo "${RDS_INFO}" | jq -r '.DBInstances[0].Engine')
RDS_PORT=$(echo "${RDS_INFO}" | jq -r '.DBInstances[0].Endpoint.Port')
RDS_ENDPOINT=$(echo "${RDS_INFO}" | jq -r '.DBInstances[0].Endpoint.Address')
VPC_ID=$(echo "${RDS_INFO}" | jq -r '.DBInstances[0].DBSubnetGroup.VpcId')
RDS_SG=$(echo "${RDS_INFO}" | jq -r '.DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId')

# Get subnets from the RDS subnet group
SUBNET_GROUP=$(echo "${RDS_INFO}" | jq -r '.DBInstances[0].DBSubnetGroup.DBSubnetGroupName')
SUBNET_IDS=$(aws rds describe-db-subnet-groups \
  --db-subnet-group-name "${SUBNET_GROUP}" \
  --region "${REGION}" \
  --query 'DBSubnetGroups[0].Subnets[*].SubnetIdentifier' \
  --output text | tr '\t' ',')

echo "  RDS ARN:    ${RDS_ARN}"
echo "  Engine:     ${RDS_ENGINE}"
echo "  Endpoint:   ${RDS_ENDPOINT}:${RDS_PORT}"
echo "  VPC:        ${VPC_ID}"
echo "  RDS SG:     ${RDS_SG}"
echo "  Subnets:    ${SUBNET_IDS}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# ── Step 1: Create Secrets Manager secret ────────────────────────
echo ""
echo "==> Step 1: Creating Secrets Manager secret..."

DB_USER="${DB_USER:-telecom_admin}"
DB_PASSWORD="${DB_PASSWORD:-}"

if [[ -z "${DB_PASSWORD}" ]]; then
  # Try to read from SSM
  DB_PASSWORD=$(aws ssm get-parameter \
    --name "/telecom-tower-power/rds-password" \
    --with-decryption \
    --query 'Parameter.Value' \
    --output text \
    --region "${REGION}" 2>/dev/null || echo "")

  if [[ -z "${DB_PASSWORD}" ]]; then
    echo "ERROR: DB password not provided via --db-password and not found in SSM."
    echo "Usage: $0 --db-password 'YourPassword'"
    exit 1
  fi
fi

SECRET_JSON=$(jq -n \
  --arg user "${DB_USER}" \
  --arg password "${DB_PASSWORD}" \
  --arg host "${RDS_ENDPOINT}" \
  --arg port "${RDS_PORT}" \
  --arg engine "${RDS_ENGINE}" \
  '{username: $user, password: $password, host: $host, port: ($port | tonumber), engine: $engine}')

SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id "${SECRET_NAME}" \
  --region "${REGION}" \
  --query 'ARN' \
  --output text 2>/dev/null || echo "")

if [[ -n "${SECRET_ARN}" && "${SECRET_ARN}" != "None" ]]; then
  echo "  Secret already exists, updating..."
  aws secretsmanager put-secret-value \
    --secret-id "${SECRET_NAME}" \
    --secret-string "${SECRET_JSON}" \
    --region "${REGION}" > /dev/null
else
  echo "  Creating new secret..."
  SECRET_ARN=$(aws secretsmanager create-secret \
    --name "${SECRET_NAME}" \
    --description "RDS credentials for Telecom Tower Power (${STAGE})" \
    --secret-string "${SECRET_JSON}" \
    --region "${REGION}" \
    --query 'ARN' \
    --output text)
fi
echo "  ✓ Secret ARN: ${SECRET_ARN}"

# ── Step 2: Create IAM role for RDS Proxy ────────────────────────
echo ""
echo "==> Step 2: Creating IAM role for RDS Proxy..."

TRUST_POLICY=$(cat <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "rds.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF
)

ROLE_ARN=$(aws iam get-role \
  --role-name "${ROLE_NAME}" \
  --query 'Role.Arn' \
  --output text 2>/dev/null || echo "")

if [[ -z "${ROLE_ARN}" || "${ROLE_ARN}" == "None" ]]; then
  ROLE_ARN=$(aws iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "${TRUST_POLICY}" \
    --description "Allows RDS Proxy to read Secrets Manager credentials" \
    --query 'Role.Arn' \
    --output text)
  echo "  Created role: ${ROLE_ARN}"
else
  echo "  Role already exists: ${ROLE_ARN}"
fi

# Attach inline policy for Secrets Manager access
SECRETS_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "secretsmanager:GetSecretValue",
      "secretsmanager:GetResourcePolicy",
      "secretsmanager:DescribeSecret",
      "secretsmanager:ListSecretVersionIds"
    ],
    "Resource": "${SECRET_ARN}"
  }, {
    "Effect": "Allow",
    "Action": "kms:Decrypt",
    "Resource": "arn:aws:kms:${REGION}:${ACCOUNT_ID}:key/*",
    "Condition": {
      "StringEquals": {
        "kms:ViaService": "secretsmanager.${REGION}.amazonaws.com"
      }
    }
  }]
}
EOF
)

aws iam put-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-name "rds-proxy-secrets-access" \
  --policy-document "${SECRETS_POLICY}"
echo "  ✓ Secrets access policy attached"

# Wait for IAM role propagation
echo "  Waiting for IAM role propagation (10s)..."
sleep 10

# ── Step 3: Create security group for Lambda ─────────────────────
echo ""
echo "==> Step 3: Creating Lambda security group..."

LAMBDA_SG=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=${LAMBDA_SG_NAME}" "Name=vpc-id,Values=${VPC_ID}" \
  --region "${REGION}" \
  --query 'SecurityGroups[0].GroupId' \
  --output text 2>/dev/null || echo "None")

if [[ "${LAMBDA_SG}" == "None" || -z "${LAMBDA_SG}" ]]; then
  LAMBDA_SG=$(aws ec2 create-security-group \
    --group-name "${LAMBDA_SG_NAME}" \
    --description "Security group for Lambda functions connecting to RDS Proxy" \
    --vpc-id "${VPC_ID}" \
    --region "${REGION}" \
    --query 'GroupId' \
    --output text)
  echo "  Created SG: ${LAMBDA_SG}"
else
  echo "  SG already exists: ${LAMBDA_SG}"
fi

# Allow Lambda SG → RDS SG on PostgreSQL port
EXISTING_RULE=$(aws ec2 describe-security-group-rules \
  --filters "Name=group-id,Values=${RDS_SG}" \
  --region "${REGION}" \
  --query "SecurityGroupRules[?ReferencedGroupInfo.GroupId=='${LAMBDA_SG}' && FromPort==\`${RDS_PORT}\`].SecurityGroupRuleId" \
  --output text 2>/dev/null || echo "")

if [[ -z "${EXISTING_RULE}" ]]; then
  aws ec2 authorize-security-group-ingress \
    --group-id "${RDS_SG}" \
    --protocol tcp \
    --port "${RDS_PORT}" \
    --source-group "${LAMBDA_SG}" \
    --region "${REGION}" > /dev/null
  echo "  ✓ Ingress rule: ${LAMBDA_SG} → ${RDS_SG}:${RDS_PORT}"
else
  echo "  ✓ Ingress rule already exists"
fi

# ── Step 4: Create RDS Proxy ────────────────────────────────────
echo ""
echo "==> Step 4: Creating RDS Proxy..."

# Check if proxy already exists
PROXY_ENDPOINT=$(aws rds describe-db-proxies \
  --db-proxy-name "${PROXY_NAME}" \
  --region "${REGION}" \
  --query 'DBProxies[0].Endpoint' \
  --output text 2>/dev/null || echo "None")

if [[ "${PROXY_ENDPOINT}" != "None" && -n "${PROXY_ENDPOINT}" ]]; then
  echo "  Proxy already exists: ${PROXY_ENDPOINT}"
else
  aws rds create-db-proxy \
    --db-proxy-name "${PROXY_NAME}" \
    --engine-family POSTGRESQL \
    --auth "[{
      \"AuthScheme\": \"SECRETS\",
      \"SecretArn\": \"${SECRET_ARN}\",
      \"IAMAuth\": \"REQUIRED\"
    }]" \
    --role-arn "${ROLE_ARN}" \
    --vpc-subnet-ids $(echo "${SUBNET_IDS}" | tr ',' ' ') \
    --vpc-security-group-ids "${RDS_SG}" \
    --require-tls \
    --idle-client-timeout 1800 \
    --region "${REGION}" > /dev/null

  echo "  Proxy created, waiting for it to become available..."
  aws rds wait db-proxy-available \
    --db-proxy-name "${PROXY_NAME}" \
    --region "${REGION}"

  PROXY_ENDPOINT=$(aws rds describe-db-proxies \
    --db-proxy-name "${PROXY_NAME}" \
    --region "${REGION}" \
    --query 'DBProxies[0].Endpoint' \
    --output text)
  echo "  ✓ Proxy available: ${PROXY_ENDPOINT}"
fi

# ── Step 5: Register target (RDS instance) ──────────────────────
echo ""
echo "==> Step 5: Registering RDS target..."

TARGET_GROUP=$(aws rds describe-db-proxy-target-groups \
  --db-proxy-name "${PROXY_NAME}" \
  --region "${REGION}" \
  --query 'TargetGroups[0].TargetGroupName' \
  --output text 2>/dev/null || echo "default")

EXISTING_TARGETS=$(aws rds describe-db-proxy-targets \
  --db-proxy-name "${PROXY_NAME}" \
  --region "${REGION}" \
  --query 'Targets[?RdsResourceId==`'"${RDS_INSTANCE}"'`].RdsResourceId' \
  --output text 2>/dev/null || echo "")

if [[ -z "${EXISTING_TARGETS}" ]]; then
  aws rds register-db-proxy-targets \
    --db-proxy-name "${PROXY_NAME}" \
    --db-instance-identifiers "${RDS_INSTANCE}" \
    --region "${REGION}" > /dev/null
  echo "  ✓ Registered ${RDS_INSTANCE} as target"
else
  echo "  ✓ Target already registered"
fi

# ── Step 6: Store proxy endpoint in SSM ──────────────────────────
echo ""
echo "==> Step 6: Storing proxy endpoint in SSM..."

SSM_PROXY_ENDPOINT="/telecom-tower-power/${STAGE}/rds-proxy-endpoint"
SSM_LAMBDA_SG="/telecom-tower-power/${STAGE}/lambda-security-group"
SSM_SUBNETS="/telecom-tower-power/${STAGE}/vpc-subnet-ids"

for PARAM_PAIR in \
  "${SSM_PROXY_ENDPOINT}:${PROXY_ENDPOINT}" \
  "${SSM_LAMBDA_SG}:${LAMBDA_SG}" \
  "${SSM_SUBNETS}:${SUBNET_IDS}"; do
  PARAM_NAME="${PARAM_PAIR%%:*}"
  PARAM_VALUE="${PARAM_PAIR#*:}"
  aws ssm put-parameter \
    --name "${PARAM_NAME}" \
    --value "${PARAM_VALUE}" \
    --type String \
    --overwrite \
    --region "${REGION}" > /dev/null
  echo "  ✓ ${PARAM_NAME} = ${PARAM_VALUE}"
done

# Build the proxy DATABASE_URL
PROXY_DB_URL="postgresql://${DB_USER}@${PROXY_ENDPOINT}:${RDS_PORT}/telecom_tower_power?sslmode=require"
aws ssm put-parameter \
  --name "/telecom-tower-power/${STAGE}/proxy-database-url" \
  --value "${PROXY_DB_URL}" \
  --type SecureString \
  --overwrite \
  --region "${REGION}" > /dev/null
echo "  ✓ Proxy DATABASE_URL stored in SSM (SecureString)"

# ── Summary ──────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo " RDS Proxy Setup Complete"
echo "=========================================="
echo ""
echo "  Proxy endpoint:  ${PROXY_ENDPOINT}"
echo "  Lambda SG:       ${LAMBDA_SG}"
echo "  VPC Subnets:     ${SUBNET_IDS}"
echo "  IAM Auth:        REQUIRED (Lambda must generate auth token)"
echo "  TLS:             REQUIRED"
echo ""
echo "Next steps:"
echo "  1. Deploy Lambda with VPC config + RDS_PROXY_HOST env var:"
echo "     ./scripts/deploy_batch_pipeline.sh \\"
echo "       --rds-proxy-host ${PROXY_ENDPOINT} \\"
echo "       --lambda-sg ${LAMBDA_SG} \\"
echo "       --vpc-subnets ${SUBNET_IDS}"
echo ""
echo "  2. Or set the proxy DATABASE_URL directly:"
echo "     ./scripts/deploy_batch_pipeline.sh \\"
echo "       --database-url '${PROXY_DB_URL}'"
echo ""
echo "  3. Lambda workers will use IAM auth tokens (auto-generated)"
echo "     No password in connection string needed."
