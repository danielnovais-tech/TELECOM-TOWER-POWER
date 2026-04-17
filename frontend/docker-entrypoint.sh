#!/bin/sh
set -e
envsubst '$PORT $BACKEND_URL' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'
