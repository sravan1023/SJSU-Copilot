#!/bin/sh
fail=0
for f in $(ls /tmp/mig/*.sql | sort); do
  name=$(basename "$f")
  err=$(psql -U postgres -v ON_ERROR_STOP=1 -q -f "$f" 2>&1)
  if [ $? -eq 0 ]; then
    printf '%-50s OK\n' "$name"
  else
    printf '%-50s FAILED\n' "$name"
    echo "$err" | head -8
    fail=1
    break
  fi
done
exit $fail
