#!/bin/sh
# Start a throwaway gunicorn on 127.0.0.1:8765 with its own empty session database, for
# deploy/smoke_test.py. Uses this checkout's code; production (port 8000) is not touched.
#   deploy/test_server.sh [DB_DIR]      (default /tmp/zunzun-test)
# Stop it with:  kill $(cat DB_DIR/gunicorn.pid)
set -eu
cd "$(dirname "$0")/.."
DIR=${1:-/tmp/zunzun-test}
mkdir -p "$DIR"
rm -f "$DIR/db.sqlite3"
export ZUNZUN_DB_PATH="$DIR/db.sqlite3" OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
python3 manage.py migrate -v0
gunicorn --workers 2 --timeout 120 --bind 127.0.0.1:8765 --pid "$DIR/gunicorn.pid" \
    --error-logfile "$DIR/gunicorn.log" --daemon wsgi:application
sleep 3
echo "test server on http://127.0.0.1:8765 (log: $DIR/gunicorn.log)"
