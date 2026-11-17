import {mergeConfig} from 'vite';
import base from './vite.config.js';
export default mergeConfig(base,{base:'/ui-app-assets/',plugins:[{name:'server-font-paths',enforce:'pre',transform(code,id){if(id.endsWith('/style.css'))return code.replaceAll("url('/fonts/","url('/theme/fonts/");}}],build:{outDir:'../../../../services/calliope/ui_app_assets',emptyOutDir:false,rollupOptions:{input:'published.html'}}});
