FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY server.mjs ./
ENV COPILOT_RUNTIME_HOST=0.0.0.0 COPILOTKIT_TELEMETRY_DISABLED=true NODE_ENV=production
EXPOSE 8766
CMD ["node", "server.mjs"]
