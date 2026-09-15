#!/bin/sh
set -eu
export UV_PROJECT_ENVIRONMENT=/tmp/hmb-verify-venv
export PYTHONPYCACHEPREFIX=/tmp/hmb-pycache
uv run --frozen --extra dev python -m compileall -q src tests
uv run --frozen --extra dev pytest -q -p no:cacheprovider
