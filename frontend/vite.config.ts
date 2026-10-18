import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { viteStaticCopy } from "vite-plugin-static-copy";

const API = "http://127.0.0.1:8765";
const RUNTIME = "http://127.0.0.1:8766";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // Silero VAD model, its AudioWorklet and the onnxruntime wasm binaries are fetched at runtime from /vad/.
    viteStaticCopy({
      targets: [
        { src: "node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js", dest: "vad", rename: { stripBase: true } },
        { src: "node_modules/@ricky0123/vad-web/dist/silero_vad_v5.onnx", dest: "vad", rename: { stripBase: true } },
        { src: "node_modules/onnxruntime-web/dist/*.wasm", dest: "vad", rename: { stripBase: true } },
        { src: "node_modules/onnxruntime-web/dist/*.mjs", dest: "vad", rename: { stripBase: true } },
      ],
    }),
  ],
  server: {
    // Same headers a production host should send for the app: no framing (clickjacking), no MIME sniffing, and
    // camera/mic only for this origin.
    headers: {
      "X-Frame-Options": "DENY",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
    },
    port: 5199,
    strictPort: true,
    proxy: {
      "/api/copilotkit": { target: RUNTIME, changeOrigin: true },
      "/api": { target: API, changeOrigin: true },
      "/ws": { target: API, ws: true, changeOrigin: true },
    },
  },
});
