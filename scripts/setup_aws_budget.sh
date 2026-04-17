#!/usr/bin/env bash
set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────
BUDGET_NAME="${BUDGET_NAME:-MonthlySpendBudget}"
BUDGET_AMOUNT="${BUDGET_AMOUNT:-10.0}"          # USD per month
ALERT_EMAIL="${ALERT_EMAIL:-}"                  # required
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

if [[ -z "$ALERT_EMAIL" ]]; then
  echo "ERROR: Set ALERT_EMAIL environment variable before running."
  echo "  Usage: ALERT_EMAIL=you@example.com BUDGET_AMOUNT=10 bash $0"
  exit 1
fi

echo "Creating AWS Budget:"
echo "  Account:  $ACCOUNT_ID"
echo "  Name:     $BUDGET_NAME"
echo "  Amount:   \$${BUDGET_AMOUNT}/month"
echo "  Alert to: $ALERT_EMAIL"
echo ""

# ─── Create the budget with two alert thresholds ─────────────────
aws budgets create-budget \
  --account-id "$ACCOUNT_ID" \
  --budget '{
    "BudgetName": "'"$BUDGET_NAME"'",
    "BudgetLimit": {
      "Amount": "'"$BUDGET_AMOUNT"'",
      "Unit": "USD"
    },
    "BudgetType": "COST",
    "TimeUnit": "MONTHLY",
    "CostTypes": {
      "IncludeTax": true,
      "IncludeSubscription": true,
      "UseBlended": false,
      "IncludeRefund": false,
      "IncludeCredit": false,
      "IncludeUpfront": true,
      "IncludeRecurring": true,
      "IncludeOtherSubscription": true,
      "IncludeSupport": true,
      "IncludeDiscount": true,
      "UseAmortized": false
    }
  }' \
  --notifications-with-subscribers '[
    {
      "Notification": {
        "NotificationType": "ACTUAL",
        "ComparisonOperator": "GREATER_THAN",
        "Threshold": 80,
        "ThresholdType": "PERCENTAGE",
        "NotificationState": "ALARM"
      },
      "Subscribers": [
        {
          "SubscriptionType": "EMAIL",
          "Address": "'"$ALERT_EMAIL"'"
        }
      ]
    },
    {
      "Notification": {
        "NotificationType": "FORECASTED",
        "ComparisonOperator": "GREATER_THAN",
        "Threshold": 100,
        "ThresholdType": "PERCENTAGE",
        "NotificationState": "ALARM"
      },
      "Subscribers": [
        {
          "SubscriptionType": "EMAIL",
          "Address": "'"$ALERT_EMAIL"'"
        }
      ]
    }
  ]'

echo ""
echo "Budget '$BUDGET_NAME' created successfully."
echo "You will be alerted when:"
echo "  - Actual spend exceeds 80% of \$${BUDGET_AMOUNT}"
echo "  - Forecasted spend exceeds 100% of \$${BUDGET_AMOUNT}"
