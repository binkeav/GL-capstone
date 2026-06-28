#!/usr/bin/env bash
set -euo pipefail

source .env
uv run --active python -m val_agent.app "$@"

