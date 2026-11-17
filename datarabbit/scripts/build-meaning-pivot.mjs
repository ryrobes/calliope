import { build } from 'vite'
import tailwind from '@tailwindcss/postcss'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
await build({
  configFile: false, root, publicDir: false,
  resolve: { alias: { '@': `${root}/src` }, dedupe: ['react', 'react-dom'] },
  esbuild: { jsx: 'automatic' },
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  css: { postcss: { plugins: [tailwind(), {
    postcssPlugin: 'meaning-shadow-property-defaults',
    OnceExit(root) {
      // @property registrations inside a shadow stylesheet are not applied by
      // Chromium. Supply the generated defaults locally, below utility rules.
      const defaults = []
      root.walkAtRules('property', rule => {
        let value = 'initial'
        rule.walkDecls('initial-value', declaration => { value = declaration.value })
        defaults.push({ prop: rule.params, value })
      })
      if (defaults.length) root.append({ name: 'layer', params: 'properties', nodes: [
        { selector: '*, ::before, ::after, ::backdrop', nodes: defaults },
      ] })
    },
  }] } },
  build: {
    lib: { entry: `${root}/src/calliope-data/meaning-pivot.tsx`, name: 'MeaningPivot', formats: ['es'], fileName: () => 'meaning-pivot.js' },
    outDir: '../services/calliope/calliope', emptyOutDir: false, cssCodeSplit: false,
    rollupOptions: { output: { assetFileNames: 'meaning-pivot.[ext]' } },
  },
})
