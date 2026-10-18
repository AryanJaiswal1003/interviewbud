# Runtime for candidate code. Mirrors LeetCode's Python 3 environment (sortedcontainers is preinstalled there).
FROM python:3.12-slim
RUN pip install --no-cache-dir sortedcontainers==2.4.0 && \
    rm -rf /root/.cache
USER 65534:65534
WORKDIR /tmp
