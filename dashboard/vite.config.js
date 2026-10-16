import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built once into dist/ (committed) — core serves it; no Node needed at runtime.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  server: { proxy: { "/api": "http://127.0.0.1:47810" } },
});
