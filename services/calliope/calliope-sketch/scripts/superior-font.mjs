import { readFileSync } from "node:fs";

const metrics = JSON.parse(readFileSync(new URL("../../superior_font_metrics.json", import.meta.url)));

// Excalidraw 0.18.1 has no public custom-font registration API. Extend its
// registry at build time, rather than relabeling a built-in font or changing
// node_modules. This covers measurement, the picker, editing, and SVG/PNG
// export. Anchors support both distributions; a changed upstream contract
// fails the build so an upgrade cannot silently lose the font.
export function patchSuperiorFont(source) {
  const family = /Virgil:\s*1,\s*Helvetica:\s*2,/;
  if (!family.test(source)) return null;
  function replaceOnce(pattern, replacement) {
    let count = 0;
    source = source.replace(pattern, (...args) => {
      count++;
      return replacement(...args);
    });
    if (count !== 1) throw new Error(`Excalidraw Superior integration: expected one ${pattern}, got ${count}`);
  }
  replaceOnce(family, (match) => `Superior: ${metrics.id}, ${match}`);
  replaceOnce(/\[([\w$]+)\.Nunito\]:\s*\{\s*metrics:\s*\{[^}]+\},\s*icon:\s*([\w$]+)\s*\}/g,
    (match, families, icon) => `${match},[${families}.Superior]:{metrics:${JSON.stringify({
      unitsPerEm: metrics.upem, ascender: metrics.ascender,
      descender: metrics.descender, lineHeight: metrics.lineHeight,
    })},icon:${icon}}`);
  replaceOnce(/([\w$]+)\("Excalifont",\s*\.\.\.([\w$]+)\)([;,])/g,
    (match, init, _faces, separator) => `${match}${init}("Superior",{uri:"./fonts/Superior/Superior-Regular.woff2"})${separator}`);
  return source;
}

export function superiorFontPlugin() {
  let patched = false;
  return {
    name: "calliope-superior-font",
    enforce: "pre",
    buildStart() {
      patched = false;
      const pkg = JSON.parse(readFileSync(new URL("../node_modules/@excalidraw/excalidraw/package.json", import.meta.url)));
      if (pkg.version !== "0.18.1") throw new Error("Review Superior font integration for this Excalidraw upgrade");
    },
    transform(source, id) {
      if (!id.includes("/@excalidraw/excalidraw/dist/")) return null;
      const result = patchSuperiorFont(source);
      if (result === null) return null;
      patched = true;
      return { code: result, map: null };
    },
    buildEnd(error) {
      if (!error && !patched) throw new Error("Excalidraw font registry was not found; Superior was not bundled");
    },
  };
}
