#!/bin/sh
# Test chatwoot_bridge on a local Odoo 19 Enterprise (helpdesk is Enterprise). Installs the
# module on an empty database and runs its tests; the database is thrown away.
# Usage: ODOO_IMAGE=<an Odoo 19 Enterprise image> sh hack/rig-test.sh [fresh|down]
# EXTRA_ADDONS=<dir> mounts more modules next to it (a module that depends on this one).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$HERE/../chatwoot_bridge" && pwd)
: "${ODOO_IMAGE:?set ODOO_IMAGE to an Odoo 19 Enterprise image}"
PG_IMAGE=${PG_IMAGE:-docker.io/library/postgres:16-alpine}
NET=chatwoot-bridge-rig
DB_CT=chatwoot-bridge-rig-db
ADDONS=/opt/bitnami/odoo/lib/odoo/addons
EXTRA=/mnt/extra-addons
LOG_DIR=${LOG_DIR:-/tmp}

up() {
  podman network exists $NET || podman network create $NET >/dev/null
  podman container exists $DB_CT || podman run -d --name $DB_CT --network $NET -e POSTGRES_USER=odoo \
    -e POSTGRES_PASSWORD=odoo -e POSTGRES_DB=postgres "$PG_IMAGE" >/dev/null
  podman start $DB_CT >/dev/null
  i=0; until podman exec $DB_CT pg_isready -U odoo -q; do i=$((i + 1)); [ $i -gt 60 ] && exit 1; sleep 1; done
}

# odoo <db> <args...>: one odoo-bin run with the module mounted read-only
odoo() {
  db=$1; shift
  path="$ADDONS,$EXTRA"
  extra=""
  if [ -n "${EXTRA_ADDONS:-}" ]; then extra="-v $EXTRA_ADDONS:/mnt/more-addons:ro"; path="$path,/mnt/more-addons"; fi
  # shellcheck disable=SC2086
  podman run --rm --network $NET --user 0 -e PATH=/opt/bitnami/python/bin:/usr/bin:/bin \
    -v "$SRC:$EXTRA/chatwoot_bridge:ro" $extra \
    "$ODOO_IMAGE" /opt/bitnami/odoo/bin/odoo-bin --db_host $DB_CT --db_user odoo --db_password odoo -d "$db" \
    --addons-path "$path" --data-dir /tmp/odoo-data --http-port 18069 --without-demo=True \
    --stop-after-init "$@"
}

drop() { podman exec $DB_CT dropdb -U odoo --if-exists "$1"; }

fresh() {
  drop cb_fresh
  odoo cb_fresh -i chatwoot_bridge --test-enable --test-tags /chatwoot_bridge --log-level=test 2>&1 |
    tee "$LOG_DIR/chatwoot-bridge-fresh.log" | grep -E "tests|FAIL|ERROR|Traceback" || true
  grep -q "0 failed, 0 error(s)" "$LOG_DIR/chatwoot-bridge-fresh.log"
}

case "${1:-fresh}" in
  fresh) up; fresh ;;
  down) podman rm -f $DB_CT >/dev/null 2>&1 || true; podman network rm $NET >/dev/null 2>&1 || true ;;
  *) echo "usage: $0 [fresh|down]" >&2; exit 2 ;;
esac
