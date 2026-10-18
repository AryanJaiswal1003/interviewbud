#!/usr/bin/env bash
# Starts Postgres, the sandbox image, the API, the CopilotKit runtime, the web app and (TTS_PROVIDER=pocket) the
# Pocket TTS server.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
[ -f .env ] || { echo "Create .env first (see Configuration in docs/architecture.md)."; exit 1; }

docker compose -f infra/docker-compose.yml up -d
docker image inspect interviewbud-sandbox:py312 >/dev/null 2>&1 || docker build -t interviewbud-sandbox:py312 infra/sandbox
(cd backend && uv sync -q --all-extras)  # extras: local Whisper (mlx on Apple Silicon)
(cd copilot-runtime && [ -d node_modules ] || npm i)
(cd frontend && [ -d node_modules ] || npm i)

env_get() { sed -n "s/^$1=//p" .env | tail -1; }

trap 'kill 0' EXIT
if [ "$(env_get TTS_PROVIDER)" = pocket ]; then
  (cd infra/pocket-tts
   [ -d .venv ] || { uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python -r requirements.txt; }
   API_KEY="$(cd "$ROOT" && env_get TTS_SERVER_API_KEY)" .venv/bin/uvicorn server:app --host 127.0.0.1 --port 8792) &
fi
(cd backend && uv run uvicorn app.main:app --port 8765) &
(cd copilot-runtime && COPILOTKIT_TELEMETRY_DISABLED=true node server.mjs) &
(cd frontend && npx vite) &
echo "Open http://localhost:5199"
wait
