/* GL Transitions: old_tv_lost_signal by mernking (gitlab: Godswork), MIT.
 * Pinned source: gl-transitions/gl-transitions@8cb8d2e8ee7b494f10046d3efb3763b8db82dbd0
 * See ARTIFACT-TRANSITION-LICENSE for source, checksum and license.
 * Only this shader is vendored; the small player below needs no runtime CDN.
 */
const effect = `
// Author: mernking gitlab: Godswork
// License: MIT

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

vec4 transition(vec2 uv) {

    float p = progress;
    float strength = sin(p * 3.14159265);

    vec2 tv = uv;

    vec4 fromColor = getFromColor(tv);
    vec4 toColor   = getToColor(tv);

    vec4 color = mix(fromColor, toColor, p);

    // horizontal tracking lines (key effect)
    float lineY = floor(tv.y * 120.0);

    float noise = hash(vec2(lineY, p * 20.0));

    float line = step(0.92, noise);

    // make lines drift during transition
    float drift =
        sin(tv.y * 30.0 + p * 10.0)
        * 0.02
        * strength;

    vec4 shiftedFrom = getFromColor(tv + vec2(drift, 0.0));
    vec4 shiftedTo   = getToColor(tv + vec2(drift, 0.0));

    vec4 lineColor = mix(shiftedFrom, shiftedTo, p);

    // apply tearing only on selected scanlines
    color = mix(color, lineColor, line * strength);

    // mild scanline darkening (CRT feel)
    float scan =
        sin(tv.y * 900.0) * 0.03;

    color.rgb -= scan * strength;

    return color;
}
`;

export function createPreviewTransition(container) {
  const canvas = document.createElement('canvas');
  canvas.className = 'artifact-build-transition';
  canvas.setAttribute('aria-hidden', 'true');
  const gl = canvas.getContext('webgl', {alpha: true, antialias: false, depth: false,
    stencil: false, premultipliedAlpha: false});
  if (!gl) throw new Error('WebGL unavailable');
  const shaders = [], textures = [];
  let program, buffer, animation = 0, complete = null, failed = null, disposed = false;
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  const stop = () => {
    cancelAnimationFrame(animation); animation = 0;
    canvas.remove(); complete = failed = null;
  };
  const onContextLost = event => { event.preventDefault(); failed?.(); };
  const onVisibility = () => { if (document.hidden || motion.matches) complete?.(); };
  const dispose = () => {
    if (disposed) return;
    disposed = true; stop();
    canvas.removeEventListener('webglcontextlost', onContextLost);
    document.removeEventListener('visibilitychange', onVisibility);
    motion.removeEventListener('change', onVisibility);
    textures.forEach(texture => gl.deleteTexture(texture));
    if (buffer) gl.deleteBuffer(buffer);
    if (program) gl.deleteProgram(program);
    shaders.forEach(shader => gl.deleteShader(shader));
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  };
  try {
    const compile = (type, source) => {
      const shader = gl.createShader(type); shaders.push(shader);
      gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error('Preview shader unavailable');
      return shader;
    };
    program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, `
      attribute vec2 position;
      varying vec2 uv;
      void main() { uv = (position + 1.0) * 0.5; gl_Position = vec4(position, 0.0, 1.0); }
    `));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, `
      precision highp float;
      varying vec2 uv;
      uniform sampler2D fromImage, toImage;
      uniform float progress, ratio, fromRatio, toRatio;
      vec4 contained(sampler2D image, vec2 p, float imageRatio) {
        p = (p - 0.5) * max(vec2(ratio / imageRatio, imageRatio / ratio), vec2(1.0)) + 0.5;
        if (p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0) return vec4(0.0);
        return texture2D(image, p);
      }
      vec4 getFromColor(vec2 p) { return contained(fromImage, p, fromRatio); }
      vec4 getToColor(vec2 p) { return contained(toImage, p, toRatio); }
      ${effect}
      void main() { gl_FragColor = transition(uv); }
    `));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('Preview shader unavailable');
    gl.useProgram(program);
    buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, 'position');
    gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    const uniforms = Object.fromEntries(['fromImage', 'toImage', 'progress', 'ratio', 'fromRatio', 'toRatio']
      .map(name => [name, gl.getUniformLocation(program, name)]));
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    for (let index = 0; index < 2; index++) {
      const texture = gl.createTexture(); textures.push(texture);
      gl.activeTexture(gl.TEXTURE0 + index); gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    gl.uniform1i(uniforms.fromImage, 0); gl.uniform1i(uniforms.toImage, 1);
    canvas.addEventListener('webglcontextlost', onContextLost);
    document.addEventListener('visibilitychange', onVisibility);
    motion.addEventListener('change', onVisibility);
    return {
      dispose,
      play(from, to, onComplete, onFailure) {
        stop();
        if (disposed || gl.isContextLost()) throw new Error('Preview context unavailable');
        [from, to].forEach((image, index) => {
          gl.activeTexture(gl.TEXTURE0 + index); gl.bindTexture(gl.TEXTURE_2D, textures[index]);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
        });
        if (gl.getError() !== gl.NO_ERROR) throw new Error('Preview texture unavailable');
        gl.uniform1f(uniforms.fromRatio, from.naturalWidth / from.naturalHeight);
        gl.uniform1f(uniforms.toRatio, to.naturalWidth / to.naturalHeight);
        complete = onComplete; failed = onFailure;
        const draw = progress => {
          const dpr = Math.min(devicePixelRatio || 1, 2);
          const width = Math.max(1, Math.round(container.clientWidth * dpr));
          const height = Math.max(1, Math.round(container.clientHeight * dpr));
          if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
          gl.viewport(0, 0, width, height);
          gl.uniform1f(uniforms.ratio, width / height);
          gl.uniform1f(uniforms.progress, progress);
          gl.drawArrays(gl.TRIANGLES, 0, 3);
        };
        // Render before inserting the overlay, so its first visible frame is
        // the previous screenshot. Keep the real images underneath as backup.
        draw(0); container.append(canvas);
        let started;
        const tick = now => {
          if (!container.isConnected || document.hidden || motion.matches) { onComplete(); return; }
          started ??= now;
          const progress = Math.min(1, (now - started) / 900);
          if (progress === 1) { onComplete(); return; }
          try { draw(progress); } catch { onFailure(); return; }
          animation = requestAnimationFrame(tick);
        };
        animation = requestAnimationFrame(tick);
        return stop;
      },
    };
  } catch (error) { dispose(); throw error; }
}
