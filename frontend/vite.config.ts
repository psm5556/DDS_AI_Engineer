import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 시 /api·/ws 요청은 FastAPI(기본 7013)로 프록시. 운영에서는 FastAPI가 dist를 직접 서빙한다.
const target = process.env.DDS_API_TARGET ?? "http://127.0.0.1:7013";
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target, changeOrigin: false },
      "/ws": { target: target.replace(/^http/, "ws"), ws: true },
    },
  },
  build: { chunkSizeWarningLimit: 6000 },
});
