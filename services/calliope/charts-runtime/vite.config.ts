import { defineConfig } from "vite";
import versions from "./package.json" with { type: "json" };

// Separate, pinned IIFEs: authored HTML can load only the core it needs.
// Retain older assets because published artifacts refer to immutable versions.
export default defineConfig(({ mode }) => {
  const runtime = mode === "table"
    ? { entry: "table", name: "table-core", version: versions.dependencies["@tanstack/table-core"] }
    : mode === "virtual"
      ? { entry: "virtual", name: "virtual-core", version: versions.dependencies["@tanstack/virtual-core"] }
      : { entry: "entry", name: "charts", version: versions.dependencies["@tanstack/charts"] };
  const filename = `rvbbit-tanstack-${runtime.name}-${runtime.version}`;
  return {
    // Library mode preserves NODE_ENV by default; hosted scripts have no Node global.
    define: { "process.env.NODE_ENV": JSON.stringify("production") },
    build: {
      lib: {
        entry: `src/${runtime.entry}.ts`,
        name: "_RvbbitTanStackBundle",
        formats: ["iife"],
        fileName: () => `${filename}.js`,
      },
      outDir: "../charts",
      emptyOutDir: false,
      minify: "oxc",
      sourcemap: false,
      reportCompressedSize: true,
      rollupOptions: { output: { assetFileNames: `${filename}.[ext]` } },
    },
  };
});
