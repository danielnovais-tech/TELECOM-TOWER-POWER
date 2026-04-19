#!/bin/sh
set -e
# Extract DNS resolver from container's resolv.conf for nginx dynamic resolution
RESOLVER=$(grep -m1 nameserver /etc/resolv.conf | awk '{print $2}')
# IPv6 addresses contain colons; wrap them in brackets for nginx
if echo "$RESOLVER" | grep -q ':'; then
  RESOLVER="[$RESOLVER]:53"
else
  RESOLVER="$RESOLVER:53"
fi
export RESOLVER
echo "Using DNS resolver: $RESOLVER"
envsubst '$PORT $BACKEND_URL $RESOLVER' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf

# Inject runtime config so the React app can read BACKEND_URL at runtime
cat > /usr/share/nginx/html/config.js <<EOF
window.__RUNTIME_CONFIG__ = {
  API_BASE: "${BACKEND_URL:-}"
};
EOF
echo "Runtime config: API_BASE=${BACKEND_URL:-'(not set)'}"

exec nginx -g 'daemon off;'
