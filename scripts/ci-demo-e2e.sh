#!/usr/bin/env bash
# Never starts locally: the Python entrypoint requires the GitHub Actions Linux VM.
set -euo pipefail
export COMPOSE_PARALLEL_LIMIT=1
export PYTHON_DOTENV_DISABLED=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
unset OPENAI_API_KEY JWT_SECRET_KEY AZURE_OPENAI_API_KEY
exec python3 -m tests.ci.demo "$@"
