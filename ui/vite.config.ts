import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// L'interface compilée est placée dans le paquet Python : `moto-blur ui` la sert telle quelle.
export default defineConfig({
  plugins: [vue()],
  base: "./",
  build: {
    outDir: "../src/moto_blur/ui",
    emptyOutDir: true,
    // Noms de fichiers fixes : l'interface compilée est commitée, autant éviter les renommages.
    rollupOptions: {
      output: { entryFileNames: "assets/app.js", chunkFileNames: "assets/[name].js", assetFileNames: "assets/app[extname]" },
    },
  },
  server: { proxy: { "/api": "http://127.0.0.1:8765", "/media": "http://127.0.0.1:8765" } },
});
