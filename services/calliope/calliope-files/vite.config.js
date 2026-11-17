import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins:[react()], define:{'process.env.NODE_ENV':JSON.stringify('production')},
  build:{lib:{entry:'src/main.jsx',name:'CalliopeFilesRuntime',formats:['iife'],fileName:()=> 'files-runtime.js'},
    outDir:'../calliope',emptyOutDir:false,cssCodeSplit:false,
    rollupOptions:{output:{assetFileNames:asset=>asset.names.some(n=>n.endsWith('.css'))?'files-runtime.css':'files-assets/[name]-[hash][extname]'}}}
});
