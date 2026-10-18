// CopilotKit runtime (v2). Thin proxy: all agent logic lives in the Python backend's AG-UI endpoints.
import express from "express";
import { HttpAgent } from "@ag-ui/client";
import { CopilotRuntime } from "@copilotkit/runtime/v2";
import { createCopilotExpressHandler } from "@copilotkit/runtime/v2/express";

const API = process.env.INTERVIEWBUD_API ?? "http://127.0.0.1:8765";
const PORT = Number(process.env.COPILOT_RUNTIME_PORT ?? 8766);
const ORIGIN = (process.env.WEB_ORIGIN ?? "http://localhost:5199").split(",");
// Local dev binds to loopback (Vite proxies to it); in production the container listens on its private network.
const HOST = process.env.COPILOT_RUNTIME_HOST ?? "127.0.0.1";

const runtime = new CopilotRuntime({
  agents: {
    interviewer: new HttpAgent({ url: `${API}/agui/interviewer` }),
    analyst: new HttpAgent({ url: `${API}/agui/analyst` }),
  },
});

const app = express();
app.use(
  createCopilotExpressHandler({
    runtime,
    basePath: "/api/copilotkit",
    cors: { origin: ORIGIN, credentials: true },
  }),
);
app.listen(PORT, HOST, () => console.log(`CopilotKit runtime on ${HOST}:${PORT} -> ${API}`));
