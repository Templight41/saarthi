#!/usr/bin/env bash
# n8n runs on Node 24: its isolated-vm native module does not build on Node 25,
# and the Docker image is unreachable behind some TLS-inspecting networks.
set -euo pipefail
NODE24="${NODE24:-/opt/homebrew/opt/node@24/bin}"
[ -x "$NODE24/node" ] || NODE24="$(dirname "$(ls -d /opt/homebrew/Cellar/node@24/*/bin/node | head -1)")"
export PATH="$NODE24:$PATH"

# Keep n8n's data with the project so it never disturbs an existing ~/.n8n,
# and so a clean reimport is just deleting this folder.
export N8N_USER_FOLDER="${N8N_USER_FOLDER:-$HOME/.saarthi-n8n/data}"
mkdir -p "$N8N_USER_FOLDER"

export N8N_PORT="${N8N_PORT:-5678}"
export N8N_HOST=localhost
export N8N_PROTOCOL=http
export WEBHOOK_URL="http://localhost:${N8N_PORT}/"
export N8N_SECURE_COOKIE=false
export N8N_BLOCK_ENV_ACCESS_IN_NODE=false
export N8N_DIAGNOSTICS_ENABLED=false
export N8N_RUNNERS_ENABLED=true
export GENERIC_TIMEZONE=Asia/Kolkata
export N8N_ENCRYPTION_KEY="${N8N_ENCRYPTION_KEY:-saarthi-dev-encryption-key}"
# 127.0.0.1, not localhost: Node resolves localhost to ::1 first, and uvicorn
# binds IPv4 only, so callbacks fail with ECONNREFUSED ::1:8000.
export SAARTHI_API_BASE="${SAARTHI_API_BASE:-http://127.0.0.1:8000}"
export SAARTHI_INTERNAL_TOKEN="${INTERNAL_API_TOKEN:-dev-internal-token}"

exec "$HOME/.saarthi-n8n/node_modules/.bin/n8n" "$@"
