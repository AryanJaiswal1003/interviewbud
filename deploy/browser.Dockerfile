# The browser that reads and fills application forms. It runs alone on the `browsing` network (shared only with the
# API): no database, no Docker socket, no secrets besides its own endpoint path. The API drives it over Playwright's
# WebSocket protocol and vets every request a page makes (public internet addresses only).
FROM mcr.microsoft.com/playwright:v1.63.0-noble
RUN npm install -g playwright@1.63.0 && npm cache clean --force
USER pwuser
EXPOSE 3000
CMD ["sh", "-c", "exec playwright run-server --port 3000 --host 0.0.0.0 --path \"/${BROWSER_PATH}\""]
