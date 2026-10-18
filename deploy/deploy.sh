#!/usr/bin/env bash
# Build and roll out InterviewBud on the production VM.   usage: deploy/deploy.sh [ssh-host]
# First run creates deploy/.env.prod with fresh secrets (Azure settings copied from .env). It is never committed.
set -euo pipefail
HOST="${1:-ib-prod}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
DOMAINS_DEFAULT="interview.getmetacognition.com, interviewbud-prod.centralindia.cloudapp.azure.com"

if [ ! -f deploy/.env.prod ]; then
  python3 - "$DOMAINS_DEFAULT" <<'PY'
import pathlib, secrets, sys
src = dict(l.split("=", 1) for l in pathlib.Path(".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
domains = sys.argv[1]
origins = ",".join("https://" + d.strip() for d in domains.split(","))
pg = secrets.token_urlsafe(24)
keep = ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_CHAT_DEPLOYMENT",
        "AZURE_OPENAI_CHAT_REASONING_EFFORT", "AZURE_OPENAI_REASONING_DEPLOYMENT", "AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT"]
lines = [f"{k}={src[k]}" for k in keep if src.get(k)] + [
    f"DOMAINS={domains}", f"WEB_ORIGINS={origins}", f"CORS_ORIGINS={origins}",
    f"POSTGRES_PASSWORD={pg}", f"DATABASE_URL=postgresql+asyncpg://interviewbud:{pg}@postgres:5432/interviewbud",
    f"SECRET_KEY={secrets.token_urlsafe(48)}", "COOKIE_SECURE=true",
    "TTS_PROVIDER=pocket", "TTS_SERVER_URL=http://tts:8792", f"TTS_SERVER_API_KEY={secrets.token_urlsafe(24)}",
    "TTS_SERVER_SAMPLE_RATE=24000", "STT_PROVIDER=local", "LOCAL_STT_MODEL=small.en", "PREP_REASONING_EFFORT=low",
    "SANDBOX_PYTHON_IMAGE=interviewbud-sandbox:py312",
]
p = pathlib.Path("deploy/.env.prod")
p.write_text("\n".join(lines) + "\n")
p.chmod(0o600)
print("created deploy/.env.prod")
PY
fi

rsync -az --delete \
  --exclude .git --exclude node_modules --exclude .venv --exclude dist --exclude __pycache__ \
  --exclude 'backend/data' --exclude '.env' --exclude 'infra/fish-speech' --exclude 'infra/chatterbox' \
  --exclude 'infra/pocket-tts/references' --exclude '.pytest_cache' \
  ./ "$HOST":~/interviewbud/
ssh "$HOST" 'chmod 600 ~/interviewbud/deploy/.env.prod && cd ~/interviewbud &&
  docker build -q -t interviewbud-sandbox:py312 infra/sandbox &&
  docker compose -f deploy/compose.prod.yml --env-file deploy/.env.prod up -d --build --remove-orphans &&
  docker compose -f deploy/compose.prod.yml --env-file deploy/.env.prod ps'
