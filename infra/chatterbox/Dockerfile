FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*

# Pinned to the commit that added Chatterbox-Nano; perth's watermarker still imports pkg_resources.
RUN pip install --no-cache-dir \
      "chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox.git@5de7a54aa4e5e2baadb0182dde554908b48b85c2" \
      "setuptools<81" fastapi==0.115.12 "uvicorn[standard]==0.34.2" ormsgpack==1.9.1 python-multipart==0.0.20

WORKDIR /app
COPY server.py .
ENV HF_HOME=/data/hf REFERENCES_DIR=/data/references PYTHONUNBUFFERED=1
EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s CMD curl -fs http://localhost:8790/v1/health || exit 1
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8790", "--workers", "1"]
