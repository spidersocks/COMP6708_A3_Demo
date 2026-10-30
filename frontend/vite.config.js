import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const brightnestProxy = (env) => {
  // Default → live SAE (matches prior localhost behavior). Local uvicorn:
  //   BRIGHTNEST_API_PROXY_TARGET=http://127.0.0.1:8000
  const target =
    env.BRIGHTNEST_API_PROXY_TARGET ||
    env.VITE_BRIGHTNEST_API_PROXY_TARGET ||
    "http://47.242.157.72:8000";
  return {
    "/brightnest-api": {
      target,
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/brightnest-api/, ""),
    },
    "/api": {
      target,
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ""),
    },
  };
};

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: brightnestProxy(env),
    },
    preview: {
      proxy: brightnestProxy(env),
    },
  };
});
