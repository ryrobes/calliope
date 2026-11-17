/* Boot-veil ASCII portrait: full-screen mp4 → ASCII, tinted to the theme.
 *
 * Faithful vanilla port of calliope-at-work2's AsciiBackdrop (itself adapted
 * from the landing hero's AsciiVideo; the edge-aware glyph matching derives
 * from Canvas UI's ASCII Object component — see that repo's
 * THIRD_PARTY_NOTICES.md). Loaded lazily, only while the launch veil is up.
 */

import * as THREE from "/calliope/vendor/three.module.min.js";

const GLYPHS = " CALLIOPE@WORKcalliopeatwork.,:;!+*xX#%&/\\|_-=()[]{}<>~";
const ATLAS_CELL = 64;
const ATLAS_PAD = 8;
const MAX_GLYPHS = 255;
const MAX_RENDER_PIXELS = 3_000_000;
const INNER_CIRCLES = [
  [0.28, 0.26], [0.72, 0.14],
  [0.28, 0.56], [0.72, 0.44],
  [0.28, 0.86], [0.72, 0.74],
];

const VERTEX_SHADER = `
out vec2 vUv;
void main() {
  vUv = position.xy * 0.5 + 0.5;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}`;

const SRGB_ENCODE = `
vec3 toSrgb(vec3 c) {
  c = clamp(c, 0.0, 1.0);
  return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(vec3(0.0031308), c));
}
`;

const CELL_FRAGMENT_SHADER = `
precision highp float;
out vec4 outColor;
uniform sampler2D tScene;
uniform sampler2D tShapes;
uniform vec2 uResolution;
uniform vec2 uCellPx;
uniform vec2 uCoverScale;
uniform vec2 uCoverOffset;
uniform int uGlyphCount;
uniform float uContrast;
uniform float uEdgeContrast;
uniform float uExposure;
${SRGB_ENCODE}
const vec2 INNER[6] = vec2[6](
  vec2(0.28, 0.26), vec2(0.72, 0.14),
  vec2(0.28, 0.56), vec2(0.72, 0.44),
  vec2(0.28, 0.86), vec2(0.72, 0.74)
);
const vec2 OUTER[10] = vec2[10](
  vec2(0.28, -0.2), vec2(0.72, -0.2),
  vec2(-0.22, 0.25), vec2(1.22, 0.25),
  vec2(-0.22, 0.5), vec2(1.22, 0.5),
  vec2(-0.22, 0.75), vec2(1.22, 0.75),
  vec2(0.28, 1.2), vec2(0.72, 1.2)
);
const vec2 RING[6] = vec2[6](
  vec2(1.0, 0.0), vec2(0.5, 0.8660254), vec2(-0.5, 0.8660254),
  vec2(-1.0, 0.0), vec2(-0.5, -0.8660254), vec2(0.5, -0.8660254)
);
vec2 cellBase;
vec2 sourceUv(vec2 uv) {
  vec2 covered = (uv - 0.5) * uCoverScale + 0.5 + uCoverOffset;
  return vec2(covered.x, 1.0 - covered.y);
}
vec4 fetchTap(vec2 p) {
  vec2 uv = p / uResolution;
  if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) return vec4(0.0);
  return texture(tScene, sourceUv(uv));
}
vec4 sampleCircle(vec2 c) {
  vec2 middle = cellBase + vec2(c.x, 1.0 - c.y) * uCellPx;
  float r = uCellPx.y * 0.161;
  vec4 acc = fetchTap(middle);
  for (int k = 0; k < 6; k++) acc += fetchTap(middle + RING[k] * r);
  return acc / 7.0;
}
float circleLum(vec4 acc) {
  vec3 straight = toSrgb(acc.rgb / max(acc.a, 1e-4));
  float level = clamp(dot(straight, vec3(0.2126, 0.7152, 0.0722)) * uExposure, 0.0, 1.0);
  return level * acc.a;
}
float dirContrast(float value, float ext) {
  float peak = max(value, ext);
  if (peak < 1e-4) return value;
  return pow(value / peak, uEdgeContrast) * peak;
}
void main() {
  cellBase = floor(gl_FragCoord.xy) * uCellPx;
  float v[6];
  vec3 colAcc = vec3(0.0);
  float alphaAcc = 0.0;
  for (int i = 0; i < 6; i++) {
    vec4 acc = sampleCircle(INNER[i]);
    v[i] = circleLum(acc);
    colAcc += acc.rgb;
    alphaAcc += acc.a;
  }
  float e[10];
  for (int i = 0; i < 10; i++) e[i] = circleLum(sampleCircle(OUTER[i]));
  v[0] = dirContrast(v[0], max(max(e[0], e[1]), max(e[2], e[4])));
  v[1] = dirContrast(v[1], max(max(e[0], e[1]), max(e[3], e[5])));
  v[2] = dirContrast(v[2], max(e[2], max(e[4], e[6])));
  v[3] = dirContrast(v[3], max(e[3], max(e[5], e[7])));
  v[4] = dirContrast(v[4], max(max(e[4], e[6]), max(e[8], e[9])));
  v[5] = dirContrast(v[5], max(max(e[5], e[7]), max(e[8], e[9])));
  float peak = max(max(max(v[0], v[1]), max(v[2], v[3])), max(v[4], v[5]));
  if (peak > 1e-4) {
    for (int i = 0; i < 6; i++) v[i] = pow(v[i] / peak, uContrast) * peak;
  }
  int best = 0;
  float bestD = 1e9;
  for (int g = 0; g < uGlyphCount; g++) {
    float d = 0.0;
    for (int i = 0; i < 6; i++) {
      float diff = v[i] - texelFetch(tShapes, ivec2(i, g), 0).r;
      d += diff * diff;
    }
    if (d < bestD) {
      bestD = d;
      best = g;
    }
  }
  vec3 cellColor = toSrgb(colAcc / max(alphaAcc, 1e-4));
  outColor = vec4(cellColor, float(best) / 255.0);
}`;

const POST_FRAGMENT_SHADER = `
precision highp float;
in vec2 vUv;
out vec4 outColor;
uniform sampler2D tCells;
uniform sampler2D tAtlas;
uniform vec2 uResolution;
uniform vec2 uCellPx;
uniform vec2 uGrid;
uniform vec2 uAtlasGrid;
uniform vec2 uAtlasPad;
uniform vec2 uAtlasInner;
uniform vec3 uBackground;
uniform vec3 uTint;
uniform float uSaturation;
uniform float uTintMix;
uniform float uIntensity;
void main() {
  vec2 fragCoord = vUv * uResolution;
  vec2 cellPos = fragCoord / uCellPx;
  vec2 cell = clamp(floor(cellPos), vec2(0.0), uGrid - 1.0);
  vec4 info = texelFetch(tCells, ivec2(cell), 0);
  float glyph = floor(info.a * 255.0 + 0.5);
  vec2 local = clamp(cellPos - cell, 0.0, 1.0);
  float gx = mod(glyph, uAtlasGrid.x);
  float gy = floor(glyph / uAtlasGrid.x);
  vec2 atlasUv = vec2(
    (gx + uAtlasPad.x + local.x * uAtlasInner.x) / uAtlasGrid.x,
    (uAtlasGrid.y - gy - 1.0 + uAtlasPad.y + local.y * uAtlasInner.y) /
      uAtlasGrid.y
  );
  vec2 atlasStep = uAtlasInner / uAtlasGrid;
  float mask = textureGrad(
    tAtlas,
    atlasUv,
    dFdx(cellPos) * atlasStep,
    dFdy(cellPos) * atlasStep
  ).a;
  vec3 glyphColor = info.rgb;
  float luma = dot(glyphColor, vec3(0.2126, 0.7152, 0.0722));
  glyphColor = mix(vec3(luma), glyphColor, uSaturation);
  vec3 tinted = uTint * pow(luma, 0.85);
  glyphColor = mix(glyphColor, tinted, uTintMix);
  glyphColor *= uIntensity;
  outColor = vec4(mix(uBackground, glyphColor, mask), 1.0);
}`;

function makeCanvas(width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width));
  canvas.height = Math.max(1, Math.round(height));
  return canvas;
}

function buildGlyphList(charset) {
  const seen = new Set([" "]);
  const glyphs = [" "];
  for (const character of charset) {
    if (glyphs.length >= MAX_GLYPHS) break;
    if (character === "\n" || character === "\r" || seen.has(character)) continue;
    seen.add(character);
    glyphs.push(character);
  }
  return glyphs;
}

function measureGlyphShapes(image, columns, cellWidth, cellHeight, count) {
  const vectors = new Float32Array(count * 6);
  const radius = cellHeight * 0.26;
  const paddedWidth = cellWidth + ATLAS_PAD * 2;
  const paddedHeight = cellHeight + ATLAS_PAD * 2;
  for (let glyph = 0; glyph < count; glyph += 1) {
    const originX = (glyph % columns) * paddedWidth + ATLAS_PAD;
    const originY = Math.floor(glyph / columns) * paddedHeight + ATLAS_PAD;
    for (let circle = 0; circle < INNER_CIRCLES.length; circle += 1) {
      const centerX = INNER_CIRCLES[circle][0] * cellWidth;
      const centerY = INNER_CIRCLES[circle][1] * cellHeight;
      let sum = 0;
      let total = 0;
      for (let y = Math.floor(centerY - radius); y <= Math.ceil(centerY + radius); y += 1) {
        for (let x = Math.floor(centerX - radius); x <= Math.ceil(centerX + radius); x += 1) {
          const dx = x + 0.5 - centerX;
          const dy = y + 0.5 - centerY;
          if (dx * dx + dy * dy > radius * radius) continue;
          total += 1;
          if (x < -ATLAS_PAD || y < -ATLAS_PAD || x >= cellWidth + ATLAS_PAD || y >= cellHeight + ATLAS_PAD) continue;
          sum += image.data[((originY + y) * image.width + originX + x) * 4 + 3];
        }
      }
      vectors[glyph * 6 + circle] = total ? sum / (total * 255) : 0;
    }
  }
  for (let circle = 0; circle < 6; circle += 1) {
    let peak = 0;
    for (let glyph = 0; glyph < count; glyph += 1) peak = Math.max(peak, vectors[glyph * 6 + circle]);
    if (peak <= 0) continue;
    for (let glyph = 0; glyph < count; glyph += 1) vectors[glyph * 6 + circle] /= peak;
  }
  return vectors;
}

function createGlyphTextures() {
  const glyphs = buildGlyphList(GLYPHS);
  const cellHeight = ATLAS_CELL;
  const cellWidth = Math.max(Math.round(cellHeight * 0.6), 8);
  const paddedWidth = cellWidth + ATLAS_PAD * 2;
  const paddedHeight = cellHeight + ATLAS_PAD * 2;
  const columns = Math.ceil(Math.sqrt(glyphs.length));
  const rows = Math.ceil(glyphs.length / columns);
  const surface = makeCanvas(columns * paddedWidth, rows * paddedHeight);
  const context = surface.getContext("2d");
  if (!context) throw new Error("2D canvas is unavailable");
  context.clearRect(0, 0, surface.width, surface.height);
  context.fillStyle = "#ffffff";
  context.textAlign = "center";
  context.textBaseline = "middle";
  const fontSize = Math.floor(Math.min(cellHeight * 0.92, cellWidth / 0.58));
  context.font = `600 ${fontSize}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
  glyphs.forEach((glyph, index) => {
    context.fillText(
      glyph,
      (index % columns) * paddedWidth + paddedWidth / 2,
      Math.floor(index / columns) * paddedHeight + paddedHeight / 2,
    );
  });
  const image = context.getImageData(0, 0, surface.width, surface.height);
  const vectors = measureGlyphShapes(image, columns, cellWidth, cellHeight, glyphs.length);
  const atlas = new THREE.CanvasTexture(surface);
  atlas.minFilter = THREE.LinearMipmapLinearFilter;
  atlas.magFilter = THREE.LinearFilter;
  atlas.wrapS = THREE.ClampToEdgeWrapping;
  atlas.wrapT = THREE.ClampToEdgeWrapping;
  const shapes = new THREE.DataTexture(vectors, 6, glyphs.length, THREE.RedFormat, THREE.FloatType);
  shapes.minFilter = THREE.NearestFilter;
  shapes.magFilter = THREE.NearestFilter;
  shapes.needsUpdate = true;
  return {
    atlas,
    atlasGrid: new THREE.Vector2(columns, rows),
    atlasInner: new THREE.Vector2(cellWidth / paddedWidth, cellHeight / paddedHeight),
    atlasPad: new THREE.Vector2(ATLAS_PAD / paddedWidth, ATLAS_PAD / paddedHeight),
    count: glyphs.length,
    shapes,
  };
}

// Theme tokens arrive as whatever CSS the theme wrote — oklch(), rgb(),
// hex — and THREE.Color only parses a subset (falling back to white on
// anything else). Rasterize through a 1px canvas so the browser does the
// color-space math for us.
function resolveCssColor(value, fallback) {
  try {
    const surface = document.createElement("canvas");
    surface.width = surface.height = 1;
    const context = surface.getContext("2d", { willReadFrequently: true });
    context.fillStyle = fallback;
    context.fillStyle = value; // invalid values leave the fallback in place
    context.fillRect(0, 0, 1, 1);
    const [r, g, b] = context.getImageData(0, 0, 1, 1).data;
    return (r << 16) | (g << 8) | b;
  } catch {
    return new THREE.Color(fallback).getHex();
  }
}

export function mountAsciiVeil(host, options = {}) {
  const src = String(options.src || "/calliope/calliope-avatar-busy-loop.mp4");
  const background = resolveCssColor(String(options.background || ""), "#0d0b09");
  const accent = resolveCssColor(String(options.accent || ""), "#d9a441");
  const intensity = Number.isFinite(options.intensity) ? options.intensity : 0.62;
  const saturation = 0.0;
  const tintMix = 1.0; // "tint" mode: duotone to the theme accent

  const canvas = document.createElement("canvas");
  canvas.className = "ascii-veil-canvas";
  host.appendChild(canvas);
  const video = document.createElement("video");
  video.muted = true;
  video.defaultMuted = true;
  video.playsInline = true;
  video.loop = true;
  video.autoplay = true;
  video.preload = "auto";
  video.tabIndex = -1;
  video.style.display = "none";
  video.src = src;
  host.appendChild(video);

  let disposed = false;
  let frameCallback = 0;
  let animationFrame = 0;
  let lastFallbackFrame = 0;
  let hasRendered = false;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      alpha: false, antialias: false, canvas, powerPreference: "high-performance",
    });
  } catch (error) {
    console.warn("ASCII veil renderer unavailable:", error);
    return { dispose() { video.remove(); canvas.remove(); } };
  }

  const bed = new THREE.Color(background);
  renderer.setClearColor(bed, 1);

  const videoTexture = new THREE.VideoTexture(video);
  videoTexture.colorSpace = THREE.SRGBColorSpace;
  videoTexture.flipY = false;
  videoTexture.generateMipmaps = false;
  videoTexture.minFilter = THREE.LinearFilter;
  videoTexture.magFilter = THREE.LinearFilter;

  const sharedResolution = new THREE.Vector2(1, 1);
  const sharedCellPixels = new THREE.Vector2(6, 10);
  const sharedGrid = new THREE.Vector2(1, 1);
  const coverScale = new THREE.Vector2(1, 1);
  const coverOffset = new THREE.Vector2(0, 0);
  const glyphTextures = createGlyphTextures();

  const cellTarget = new THREE.WebGLRenderTarget(1, 1, {
    depthBuffer: false, stencilBuffer: false,
    minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter,
  });

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3),
  );

  const cellMaterial = new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3,
    vertexShader: VERTEX_SHADER,
    fragmentShader: CELL_FRAGMENT_SHADER,
    uniforms: {
      tScene: { value: videoTexture },
      tShapes: { value: glyphTextures.shapes },
      uResolution: { value: sharedResolution },
      uCellPx: { value: sharedCellPixels },
      uCoverScale: { value: coverScale },
      uCoverOffset: { value: coverOffset },
      uGlyphCount: { value: glyphTextures.count },
      uContrast: { value: 1.45 },
      uEdgeContrast: { value: 3.25 },
      uExposure: { value: 1.08 },
    },
    depthTest: false, depthWrite: false, blending: THREE.NoBlending,
  });
  const cellMesh = new THREE.Mesh(geometry, cellMaterial);
  cellMesh.frustumCulled = false;
  const cellScene = new THREE.Scene();
  cellScene.add(cellMesh);

  const postMaterial = new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3,
    vertexShader: VERTEX_SHADER,
    fragmentShader: POST_FRAGMENT_SHADER,
    uniforms: {
      tCells: { value: cellTarget.texture },
      tAtlas: { value: glyphTextures.atlas },
      uResolution: { value: sharedResolution },
      uCellPx: { value: sharedCellPixels },
      uGrid: { value: sharedGrid },
      uAtlasGrid: { value: glyphTextures.atlasGrid },
      uAtlasPad: { value: glyphTextures.atlasPad },
      uAtlasInner: { value: glyphTextures.atlasInner },
      uBackground: { value: bed },
      uTint: { value: new THREE.Color(accent) },
      uSaturation: { value: saturation },
      uTintMix: { value: tintMix },
      uIntensity: { value: intensity },
    },
    depthTest: false, depthWrite: false, blending: THREE.NoBlending,
  });
  const postMesh = new THREE.Mesh(geometry, postMaterial);
  postMesh.frustumCulled = false;
  const postScene = new THREE.Scene();
  postScene.add(postMesh);
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  function resize() {
    if (disposed) return;
    const width = Math.max(canvas.clientWidth, 1);
    const height = Math.max(canvas.clientHeight, 1);
    const preferredRatio = Math.min(window.devicePixelRatio || 1, 1.5);
    const pixelRatio = Math.min(preferredRatio, Math.sqrt(MAX_RENDER_PIXELS / (width * height)));
    renderer.setPixelRatio(Math.max(pixelRatio, 0.75));
    renderer.setSize(width, height, false);
    const deviceWidth = canvas.width;
    const deviceHeight = canvas.height;
    sharedResolution.set(deviceWidth, deviceHeight);
    const cellSize = width < 720 ? 9 : width > 1800 ? 13 : 11;
    const cellHeight = cellSize * Math.max(pixelRatio, 0.75);
    const cellWidth = cellHeight * 0.6;
    sharedCellPixels.set(cellWidth, cellHeight);
    const columns = Math.max(Math.ceil(deviceWidth / cellWidth), 1);
    const rows = Math.max(Math.ceil(deviceHeight / cellHeight), 1);
    sharedGrid.set(columns, rows);
    cellTarget.setSize(columns, rows);
    const viewportAspect = width / height;
    const videoAspect = video.videoWidth > 0 && video.videoHeight > 0
      ? video.videoWidth / video.videoHeight : 1;
    if (viewportAspect > videoAspect) coverScale.set(1, videoAspect / viewportAspect);
    else coverScale.set(viewportAspect / videoAspect, 1);
  }

  function renderFrame() {
    if (disposed || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
    videoTexture.needsUpdate = true;
    renderer.setRenderTarget(cellTarget);
    renderer.render(cellScene, camera);
    renderer.setRenderTarget(null);
    renderer.render(postScene, camera);
    if (!hasRendered) {
      hasRendered = true;
      host.dataset.ready = "true";
    }
  }

  function stopFrameLoop() {
    if (frameCallback && "cancelVideoFrameCallback" in video) {
      video.cancelVideoFrameCallback(frameCallback);
    }
    cancelAnimationFrame(animationFrame);
    frameCallback = 0;
    animationFrame = 0;
  }

  function fallbackFrameLoop(now) {
    if (disposed || document.hidden || video.paused) return;
    if (now - lastFallbackFrame >= 1000 / 24) {
      lastFallbackFrame = now;
      renderFrame();
    }
    animationFrame = requestAnimationFrame(fallbackFrameLoop);
  }

  function startFrameLoop() {
    stopFrameLoop();
    if (disposed || reducedMotion || document.hidden) return;
    if ("requestVideoFrameCallback" in video) {
      const onVideoFrame = () => {
        if (disposed || document.hidden || video.paused) return;
        renderFrame();
        frameCallback = video.requestVideoFrameCallback(onVideoFrame);
      };
      frameCallback = video.requestVideoFrameCallback(onVideoFrame);
    } else {
      animationFrame = requestAnimationFrame(fallbackFrameLoop);
    }
  }

  function showReducedMotionFrame() {
    const renderStill = () => { videoTexture.needsUpdate = true; renderFrame(); };
    video.pause();
    if (video.duration > 1.25) {
      video.addEventListener("seeked", renderStill, { once: true });
      video.currentTime = 1.25;
    } else {
      renderStill();
    }
  }

  function beginPlayback() {
    resize();
    if (reducedMotion) { showReducedMotionFrame(); return; }
    video.play().then(() => { renderFrame(); startFrameLoop(); }).catch(() => renderFrame());
  }

  const resizeObserver = new ResizeObserver(() => { resize(); renderFrame(); });
  resizeObserver.observe(canvas);
  video.addEventListener("loadeddata", beginPlayback);
  resize();
  if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) beginPlayback();

  return {
    dispose() {
      if (disposed) return;
      disposed = true;
      stopFrameLoop();
      resizeObserver.disconnect();
      video.removeEventListener("loadeddata", beginPlayback);
      video.pause();
      video.removeAttribute("src");
      video.load();
      videoTexture.dispose();
      glyphTextures.atlas.dispose();
      glyphTextures.shapes.dispose();
      cellTarget.dispose();
      cellMaterial.dispose();
      postMaterial.dispose();
      geometry.dispose();
      renderer.dispose();
      video.remove();
      canvas.remove();
    },
  };
}
