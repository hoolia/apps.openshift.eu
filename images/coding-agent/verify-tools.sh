#!/bin/sh
# every check runs as the arbitrary UID the image runs as; one failure fails the build
set -u
failed=0
for list in "$@"; do
  while IFS= read -r check; do
    [ -n "$check" ] || continue
    if sh -c "$check" >/dev/null 2>&1; then
      echo "ok   $check"
    else
      echo "FAIL $check"
      failed=1
    fi
  done < "$list"
done
exit "$failed"
