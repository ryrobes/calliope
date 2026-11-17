import {defineConfig} from 'vite';

// @a2ui/react 0.12.0 publishes the CSS but loses its CSS-module lookup objects.
// Restore those names at build time; rendering, bindings and actions stay upstream.
export default defineConfig({plugins:[{
  name:'a2ui-0.12-css-module-repair',
  enforce:'pre',
  transform(code,id) {
    if (!id.endsWith('/@a2ui/react/v0_9/index.js')) return;
    return code.replace(/var (\w+_default) = \{\};/g, (match,name) => {
      const properties = [...new Set([...code.matchAll(new RegExp(name+'\\.(\\w+)','g'))].map(m=>m[1]))];
      return properties.length ? `var ${name} = ${JSON.stringify(Object.fromEntries(properties.map(p=>[p,p])))};` : match;
    });
  }
} ]});
