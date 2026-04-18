#!/bin/sh
set -e
# Extract DNS resolver from container's resolv.conf for nginx dynamic resolution
export RESOLVER=$(grep -m1 nameserver /etc/resolv.conf | awk '{print $2}')
echo "Using DNS resolver: $RESOLVER"
envsubst '$PORT $BACKEND_URL $RESOLVER' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'
