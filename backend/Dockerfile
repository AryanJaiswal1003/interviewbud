FROM python:3.12-slim
# ffmpeg: voice-sample conversion. docker CLI: the API starts and runs the code sandbox on the host's Docker.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_HTTP_TIMEOUT=300 UV_HTTP_RETRIES=5 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH \
    HF_HOME=/app/data/hf PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra local-stt --no-install-project
COPY app ./app
EXPOSE 8765
# One worker: live interview sessions are in-process. Behind Caddy, so trust its X-Forwarded-For (the API port is
# only reachable on the private Compose network).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers", "--forwarded-allow-ips", "*"]
