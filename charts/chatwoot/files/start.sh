#!/bin/sh
# Starts a Chatwoot command with
# - FRONTEND_URL: the value, or the address the route-host init container read from the Route
# - with a bucket (BUCKET_HOST from the ObjectBucketClaim): STORAGE_ENDPOINT, http on port
#   80 and https on any other port, and with CHART_S3_EXTRA_CA a trust bundle for the S3
#   client of the system certificates plus that one, so a bucket signed by the cluster and
#   one with a public certificate both verify
set -e

# Ruby rejects the shared OpenShift emptyDir when it is world-writable
# without a sticky bit. Use a private directory owned by the runtime UID.
TMPDIR=$(mktemp -d "${CHART_TMP_PARENT:-/app/tmp}/chatwoot.XXXXXX")
chmod 700 "$TMPDIR"
export TMPDIR

if [ -z "$FRONTEND_URL" ]; then
  FRONTEND_URL=$(cat /run/chatwoot/frontend-url)
  export FRONTEND_URL
fi
if [ -n "$BUCKET_HOST" ]; then
  port=${BUCKET_PORT:-443}
  scheme=https
  [ "$port" = 80 ] && scheme=http
  STORAGE_ENDPOINT="$scheme://$BUCKET_HOST:$port"
  export STORAGE_ENDPOINT
fi
if [ -n "$CHART_S3_EXTRA_CA" ]; then
  system=${CHART_SYSTEM_CA:-/etc/ssl/certs/ca-certificates.crt}
  bundle=${CHART_S3_BUNDLE:-/tmp/s3-ca-bundle.pem}
  [ -s "$system" ] || { echo "no system certificates at $system" >&2; exit 1; }
  [ -s "$CHART_S3_EXTRA_CA" ] || { echo "no certificate at $CHART_S3_EXTRA_CA" >&2; exit 1; }
  cat "$system" "$CHART_S3_EXTRA_CA" > "$bundle"
  AWS_CA_BUNDLE=$bundle
  export AWS_CA_BUNDLE
fi
exec "$@"
