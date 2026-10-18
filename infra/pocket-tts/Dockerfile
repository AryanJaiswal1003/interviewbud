FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
# CPU-only PyTorch: Pocket TTS runs on CPU, and the CUDA wheels would add gigabytes for nothing.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
COPY server.py ./
ENV HF_HOME=/data/hf REFERENCES_DIR=/data/references PYTHONUNBUFFERED=1
EXPOSE 8792
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8792", "--workers", "1"]
