#!/bin/sh
set -e
railway/code-change-WejzE2
# Extract DNS resolver from container's resolv.conf for nginx dynamic resolution
RESOLVER=$(grep -m1 nameserver /etc/resolv.conf | awk '{print $2}')
# IPv6 addresses contain colons; wrap them in brackets for nginx
if echo "$RESOLVER" | grep -q ':'; then
  RESOLVER="[$RESOLVER]:53"
else
  RESOLVER="$RESOLVER:53"
fi
export RESOLVER

# Extract DNS resolver, wrapping IPv6 addresses in brackets for nginx
export RESOLVER=$(awk '/^nameserver/{
  if ($2 ~ /:/) printf "[%s]:53", $2;
  else printf "%s:53", $2;
  exit
}' /etc/resolv.conf)
main
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
