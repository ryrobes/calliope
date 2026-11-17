/*
 * Parametric ray waiting animation for Calliope's primary chat response.
 *
 * Adapted from the Omarchy Plugin Marketplace hero animation. The original
 * implementation and these adaptations are distributed under the MIT License.
 * Copyright (c) 2026 HANCORE
 * https://github.com/HANCORE-linux/omarchy-plugin-marketplace
 */
(() => {
  "use strict";

  const active = new WeakMap();
  const base = {
    AMP: 4, WIND: 35, VS: 7, VO: 13, QA: 2, QF: 3, SP: 35, TH: 9,
    ORB: 40, YS: 35, PD: 9, PSP: 2, WV: 9, WSP: 2, DOF: 4,
    RF: 9, DPH: 2, CX: 200, CY: 0, DENS: 235,
  };
  const presets = [
    { name: "original", zoom: 1.08, offsetY: -45, values: {} },
    { name: "cocoon", zoom: 1.04, offsetY: -105, values: { WIND: 14.5, AMP: 2.6, TH: 14.2, SP: 70, ORB: 22, YS: 52, RF: 4.2, DENS: 150 } },
    { name: "storm", zoom: 0.92, values: { PD: 2.4, PSP: 4.2, WV: 2.8, RF: 19, DPH: -3.4, WIND: 48, DENS: 110 } },
    { name: "ray", zoom: 1.18, values: { AMP: 8.69, WIND: 38.26, VS: 16.38, VO: 11.75, QA: 1.65, QF: 3.47, SP: 38.62, TH: 9.63, ORB: 47.63, YS: 7.34, PD: 10.77, PSP: 2.73, WV: 7.21, WSP: 3.79, DOF: 5.98, RF: 3.04, DPH: 3.18, CX: 201, CY: 161 } },
    { name: "bird", zoom: 1.08, values: { AMP: 9.07, WIND: 73.68, VS: 15.45, VO: 25.38, QA: 4.98, QF: 5.32, SP: 44.61, TH: 9.37, ORB: 16.84, YS: 21.85, PD: 12.64, PSP: 3.52, WV: 10.31, WSP: 2, DOF: 3.3, RF: 10.2, DPH: 2.76, CX: 200, CY: -261 } },
    { name: "wing", zoom: 1.18, values: { AMP: 7.18, WIND: 47.39, VS: 16.24, VO: 28.23, QA: 3.58, QF: 5.84, SP: 38.57, TH: 12.2, ORB: 25.09, YS: 10.8, PD: 15.4, PSP: 3.23, WV: 12.94, WSP: 1.19, DOF: 8.59, RF: 10.94, DPH: 0.79, CX: 205, CY: -5 } },
  ].map((preset) => ({ ...preset, values: { ...base, ...preset.values } }));
  const presetByName = new Map(presets.map((preset) => [preset.name, preset]));
  const pointCount = 3600;
  const sourcePointCount = 6000;
  const sourceIndices = new Float32Array(pointCount);

  for (let index = 0; index < pointCount; index += 1) {
    sourceIndices[index] = index * (sourcePointCount / pointCount);
  }

  function mount(canvas, presetName = "ray") {
    if (!(canvas instanceof HTMLCanvasElement)) return;
    active.get(canvas)?.();

    const context = canvas.getContext("2d");
    if (!context) return;

    const preset = presetByName.get(String(presetName || "").toLowerCase()) || presets[3];
    const zoom = Math.max(0.5, Math.min(3, Number(canvas.dataset.rayZoom) || 1));
    const motionPreference = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    let reducedMotion = motionPreference?.matches === true;
    const startedAt = performance.now();
    let lastDrawAt = startedAt;
    let time = 0;
    const targetSpeed = () => {
      const value = Number(canvas.dataset.raySpeed ?? 1);
      return Number.isFinite(value) ? Math.max(0, Math.min(2, value)) : 1;
    };
    let speed = targetSpeed();
    let width = 1;
    let height = 1;
    let primary = "#c9c3b7";
    let accent = "#68c7b2";
    let frame = 0;
    let visible = true;
    let stopped = false;
    let nextDrawAt = 0;
    // A speed of zero means "come to rest": the shape glides to a stop, the
    // last frame stays on the canvas, and no more frames are requested until
    // the speed changes. An idle page should cost nothing to keep open.
    const resting = () => targetSpeed() === 0 && speed < .01;

    if (canvas.getAttribute("aria-hidden") !== "true") {
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", "Calliope is thinking");
    }

    const updateColors = () => {
      const styles = window.getComputedStyle(canvas);
      const rootStyles = window.getComputedStyle(document.documentElement);
      primary = styles.color || primary;
      accent = rootStyles.getPropertyValue("--jade").trim() || accent;
      // An accent-only instance inherits the theme's resolved CSS color,
      // including palettes supplied through variables or color-mix().
      if (canvas.dataset.rayColor === "accent") accent = primary;
    };

    const resize = () => {
      const bounds = canvas.getBoundingClientRect();
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, bounds.width);
      height = Math.max(1, bounds.height);
      canvas.width = Math.round(width * pixelRatio);
      canvas.height = Math.round(height * pixelRatio);
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    };

    const draw = (now) => {
      context.clearRect(0, 0, width, height);
      const values = preset.values;
      // Integrate elapsed time so changing speed never jumps to a new shape.
      // Ease over the handoff instead of remounting an idle/active instance.
      const elapsed = Math.max(0, Math.min(100, now - lastDrawAt));
      lastDrawAt = now;
      if (!reducedMotion) {
        speed += (targetSpeed() - speed) * (1 - Math.exp(-elapsed / 800));
        time += elapsed * .00105 * speed;
      }
      const scale = Math.min(width / 400, height / 400) * preset.zoom * zoom;
      const originX = (width - 400 * scale) / 2;
      const originY = (height - 400 * scale) / 2;

      for (let index = pointCount; index--;) {
        const sourceIndex = sourceIndices[index];
        const y = sourceIndex / values.DENS;
        const k = (values.AMP + Math.cos(sourceIndex / values.PD - time * values.PSP))
          * Math.cos(sourceIndex / values.WIND);
        const e = y / values.VS - values.VO;
        const distance = Math.hypot(k, e)
          + Math.sin(e / values.WV + time / values.WSP) - values.DOF;
        const q = values.QA * Math.sin(k * values.QF)
          - y / values.SP * k
            * (values.TH + k
              * Math.sin(Math.cos(e) * values.RF - distance * values.DPH + time));
        const angle = distance - time;
        const x = originX + (q + values.ORB * Math.cos(angle) + values.CX) * scale;
        const pointY = originY
          + (q * Math.sin(angle) + distance * values.YS + values.CY + (preset.offsetY || 0)) * scale;

        if (x < 0 || x > width || pointY < 0 || pointY > height) continue;
        context.globalAlpha = index % 13 === 0 ? 0.52 : 0.29;
        context.fillStyle = index % 19 === 0 ? accent : primary;
        const size = index % 29 === 0 ? 1.25 : 0.78;
        context.fillRect(x, pointY, size, size);
      }
      context.globalAlpha = 1;
    };

    const stop = () => {
      if (stopped) return;
      stopped = true;
      window.cancelAnimationFrame(frame);
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
      themeObserver.disconnect();
      speedObserver.disconnect();
      document.removeEventListener("visibilitychange", handleVisibility);
      motionPreference?.removeEventListener("change", handleMotionPreference);
      active.delete(canvas);
    };

    const tick = (now) => {
      frame = 0;
      if (stopped) return;
      if (!canvas.isConnected) {
        stop();
        return;
      }
      if (!visible || document.visibilityState !== "visible") return;
      if (!nextDrawAt || now >= nextDrawAt) {
        nextDrawAt = now + 1000 / (speed < .15 ? 12 : 30) - 1;
        draw(now);
      }
      if (resting()) {
        speed = 0;
        return; // asleep: speedObserver wakes the loop
      }
      frame = window.requestAnimationFrame(tick);
    };

    const sync = () => {
      if (stopped) return;
      const shouldRun = !reducedMotion && !resting() && visible && document.visibilityState === "visible";
      if (shouldRun && !frame) frame = window.requestAnimationFrame(tick);
      if (!shouldRun && frame) {
        window.cancelAnimationFrame(frame);
        frame = 0;
      }
    };
    const handleVisibility = () => sync();
    const handleMotionPreference = (event) => {
      reducedMotion = event.matches;
      lastDrawAt = performance.now();
      sync();
      if (reducedMotion) draw(lastDrawAt);
    };

    // Nothing repaints a still canvas, so the changes that would leave it
    // blank or stale (a new size, a new theme) redraw it once.
    const still = () => reducedMotion || resting();
    const resizeObserver = new ResizeObserver(() => {
      resize();
      if (still()) window.requestAnimationFrame(draw);
    });
    const intersectionObserver = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      sync();
    });
    const themeObserver = new MutationObserver(() => {
      updateColors();
      if (still()) window.requestAnimationFrame(draw);
    });
    const speedObserver = new MutationObserver(() => {
      lastDrawAt = performance.now();
      sync();
    });

    resizeObserver.observe(canvas);
    intersectionObserver.observe(canvas);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "style"],
    });
    speedObserver.observe(canvas, { attributes: true, attributeFilter: ["data-ray-speed"] });
    document.addEventListener("visibilitychange", handleVisibility);
    motionPreference?.addEventListener("change", handleMotionPreference);
    updateColors();
    resize();

    if (still()) draw(startedAt);
    else sync();

    active.set(canvas, stop);
  }

  function unmount(canvas) {
    active.get(canvas)?.();
  }

  function unmountAll(root = document) {
    root.querySelectorAll("canvas[data-calliope-ray]").forEach(unmount);
  }

  function mountAll(root = document) {
    root.querySelectorAll("canvas[data-calliope-ray]").forEach((canvas) => {
      if (!active.has(canvas)) mount(canvas, canvas.dataset.calliopeRay);
    });
  }

  window.CalliopeParametricRays = {
    mount,
    unmount,
    unmountAll,
    mountAll,
    presets: presets.map((preset) => preset.name),
  };
})();
