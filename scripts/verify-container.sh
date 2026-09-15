#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILDER='cgr.dev/chainguard/python@sha256:3d1f8858036c90e826f267baa1abbfd1b59e16d23b0d58ee8d84f4f216375702'

run_uv() {
  docker run --rm \
    -v "$ROOT:/work:ro" \
    -w /work \
    -e UV_PROJECT_ENVIRONMENT=/tmp/hmb-verify-venv \
    -e PYTHONPYCACHEPREFIX=/tmp/hmb-pycache \
    --entrypoint /usr/bin/uv \
    "$BUILDER" "$@"
}

run_uv run --frozen --extra dev python -m compileall -q src tests
run_uv run --frozen --extra dev pytest -q -p no:cacheprovider
