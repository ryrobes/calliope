import { build } from 'vite'
import tailwind from '@tailwindcss/postcss'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
await build({
  configFile: false, root, publicDir: false,
  resolve: { alias: { '@': `${root}/src` }, dedupe: ['react', 'react-dom'] },
  esbuild: { jsx: 'automatic' },
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  css: { postcss: { plugins: [tailwind()] } },
  build: {
    lib: { entry: `${root}/src/calliope-data/main.tsx`, name: 'CalliopeData', formats: ['iife'], fileName: () => 'data-runtime.js' },
    outDir: '../services/calliope/calliope', emptyOutDir: false, cssCodeSplit: false,
    rollupOptions: { output: { assetFileNames: 'data-runtime.[ext]' } },
  },
})
