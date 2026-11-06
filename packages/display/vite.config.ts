import { svelte } from "@sveltejs/vite-plugin-svelte";
import { resolve } from "node:path";
import { defineConfig } from "vite";

const root = import.meta.dirname;

export default defineConfig({
  plugins: [svelte()],
  // Relative base so the built page works when served from the Python
  // desktop server at any path, or opened directly.
  base: "./",
  build: {
    outDir: "dist",
    target: "es2022",
    rollupOptions: {
      input: {
        main: resolve(root, "index.html"), // on-air display
        control: resolve(root, "control.html"), // desktop settings page
      },
    },
  },
});
