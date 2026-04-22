TS=$(date +%s)
API=https://api.telecomtowerpower.com.br
declare -A EXPECT
EXPECT[pro]="price_1TOo6g3HxrWvYaypEsfw82gL"
EXPECT[enterprise]="price_1TOo8H3HxrWvYaypw4DHPiYe"

KEY=$(cat /home/daniel/TELECOM-TOWER-POWER/secrets/stripe_secret_key)

PASS=0; FAIL=0
for tier in pro enterprise; do
    email="qa+${tier}_${TS}@telecomtowerpower.com.br"
    echo "=== $tier ($email) ==="
    
    # Retry logic for 503
    for attempt in 1 2 3; do
      resp=$(curl -sS -w "\n__HTTP__%{http_code}" -X POST "$API/signup/checkout" \
        -H 'Content-Type: application/json' \
        -d "{\"email\":\"$email\",\"tier\":\"$tier\"}")
      code=$(echo "$resp" | grep -o '__HTTP__[0-9]*' | cut -d_ -f5)
      body=$(echo "$resp" | sed 's/__HTTP__[0-9]*$//')
      
      if [[ "$code" == "503" ]]; then
        echo "Attempt $attempt: HTTP 503. Retrying in 20s..."
        sleep 20
      else
        break
      fi
    done

    echo "HTTP $code"
    if [[ "$code" != "200" ]]; then
      echo "FAIL: $code - $body"
      FAIL=$((FAIL+1))
      continue
    fi

    url=$(echo "$body" | jq -r '.checkout_url // empty')
    if [[ -z "$url" ]]; then
      echo "FAIL: no checkout_url"
      FAIL=$((FAIL+1))
      continue
    fi

    # Extract session id from URL
    sid=$(echo "$url" | grep -oE 'cs_(live|test)_[A-Za-z0-9]+' | head -1)
    echo "session=$sid"
    
    # Retrieve line items from Stripe API
    price_id=$(curl -sS -u "$KEY:" \
      "https://api.stripe.com/v1/checkout/sessions/$sid/line_items" \
      | jq -r '.data[0].price.id')
    
    echo "price_id=$price_id  expected=${EXPECT[$tier]}"
    if [[ "$price_id" == "${EXPECT[$tier]}" ]]; then
      echo "PASS"
      PASS=$((PASS+1))
    else
      echo "FAIL: price mismatch"
      FAIL=$((FAIL+1))
    fi
done
echo "=========================="
echo "RESULT: $PASS passed, $FAIL failed"
