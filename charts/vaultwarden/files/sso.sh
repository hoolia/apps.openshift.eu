#!/bin/sh
# Runs Vaultwarden (the arguments) with the single sign-on client of the optional Secret
# volume SSO_DIR while it holds one. Every INTERVAL seconds the client is read again; when
# it appeared, changed or went, Vaultwarden restarts in this container.
dir=${SSO_DIR:-/etc/vaultwarden-sso}
interval=${INTERVAL:-30}

client() {
  cat "$dir/$KEY_ISSUER" "$dir/$KEY_CLIENT_ID" "$dir/$KEY_CLIENT_SECRET" 2>/dev/null | cksum
}

start() {
  if [ -s "$dir/$KEY_ISSUER" ] && [ -s "$dir/$KEY_CLIENT_ID" ]; then
    echo "sso: single sign-on on: issuer $(cat "$dir/$KEY_ISSUER"), client $(cat "$dir/$KEY_CLIENT_ID")"
    (
      SSO_ENABLED=true
      SSO_AUTHORITY=$(cat "$dir/$KEY_ISSUER")
      SSO_CLIENT_ID=$(cat "$dir/$KEY_CLIENT_ID")
      SSO_CLIENT_SECRET=$(cat "$dir/$KEY_CLIENT_SECRET" 2>/dev/null)
      export SSO_ENABLED SSO_AUTHORITY SSO_CLIENT_ID SSO_CLIENT_SECRET
      exec "$@"
    ) &
  else
    echo "sso: single sign-on off: no client in the Secret"
    "$@" &
  fi
  pid=$!
}

# a reaped or zombie child has ended
running() {
  state=$(sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | cut -c1)
  [ -n "$state" ] && [ "$state" != Z ]
}

stop() {
  kill -TERM "$pid" 2>/dev/null
  wait "$pid"
}

trap 'stop; exit 0' TERM INT
current=$(client)
start "$@"
while :; do
  i=0
  while [ "$i" -lt "$interval" ]; do
    if ! running "$pid"; then
      wait "$pid"
      code=$?
      echo "sso: Vaultwarden exited with $code"
      exit "$code"
    fi
    sleep 1
    i=$((i + 1))
  done
  new=$(client)
  if [ "$new" != "$current" ]; then
    echo "sso: client changed, restarting"
    current=$new
    stop
    start "$@"
  fi
done
