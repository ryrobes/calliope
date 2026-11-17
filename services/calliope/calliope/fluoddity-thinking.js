/*
 * Fluoddity Lite: a deliberately small WebGPU thought field for Calliope.
 *
 * The trail-sensing particles and ping-pong trail field are adapted from the
 * ideas and WebGPU implementation in Fluoddity-Web. This is not its editor:
 * no controls, persistence, picking, recording, bloom, or motion blur ship in
 * this file. The existing parametric ray remains the accessibility and
 * unsupported-hardware fallback, but is suspended while WebGPU is active.
 *
 * MIT License — Copyright (c) 2026 Jesse Gelders (aphid91)
 * https://github.com/aphid91/Fluoddity-Web
 * See FLUODDITY-LICENSE beside this file.
 */
(() => {
  "use strict";

  const PARTICLE_COUNT = 12_000;
  const PARTICLE_FLOATS = 8;
  const PARTICLE_STRIDE = PARTICLE_FLOATS * 4;
  const WORKGROUP_SIZE = 64;
  const TARGET_FPS = 30;
  const FRAME_INTERVAL = 1000 / TARGET_FPS;
  const THROTTLED_FPS = 15;
  const THROTTLED_FRAME_INTERVAL = 1000 / THROTTLED_FPS;
  const PIXEL_RATIO_CAP = 1.25;
  const TRAIL_WIDTH_CAP = 520;
  const STARTUP_TIMEOUT_MS = 2500;
  const AUTO_PROBE_BUDGET_MS = 24;
  const AUTO_FALLBACK_BUDGET_MS = 90;
  const RUNTIME_BACKLOG_BUDGET_MS = 30;
  const RUNTIME_BACKLOG_LIMIT = 3;
  const RUNTIME_RECOVERY_LIMIT = 3;
  const EVOLUTION_INTERVAL_MS = 60_000;
  const MODE_STORAGE_KEY = "calliope-thinking-renderer-v1";
  const TRAIL_FORMAT = "rgba8unorm";
  const VISUALS = ["brain", "tangle", "medusa", "starcrossed"];

  const active = new WeakMap();
  const live = new Set();
  let runtimePromise = null;
  let lostRuntime = false;
  let lastDecision = { mode: "auto", state: "idle", reason: "", probe_ms: null, visual: null };

  const computeShader = /* wgsl */ `
struct Particle {
  pos: vec2f,
  heading: f32,
  speed: f32,
  noise: vec2f,
  tone: f32,
  lane: f32,
}

struct SimUniforms {
  time: f32,
  delta: f32,
  width: f32,
  height: f32,
  seed: f32,
  aspect: f32,
  point_size: f32,
  variant: f32,
}

@group(0) @binding(0) var<storage, read> source_particles: array<Particle>;
@group(0) @binding(1) var<storage, read_write> target_particles: array<Particle>;
@group(0) @binding(2) var trail_texture: texture_2d<f32>;
@group(0) @binding(3) var trail_sampler: sampler;
@group(0) @binding(4) var<uniform> u: SimUniforms;

const PI: f32 = 3.14159265359;
const TAU: f32 = 6.28318530718;

fn hash(value: f32) -> f32 {
  return fract(sin(value * 12.9898 + u.seed * 0.00031) * 43758.5453);
}

fn wrap_angle(value: f32) -> f32 {
  return atan2(sin(value), cos(value));
}

fn sensor_offset(angle: f32, distance: f32) -> vec2f {
  return vec2f(cos(angle) / max(u.aspect, 0.01), sin(angle)) * distance;
}

fn to_world(pos: vec2f) -> vec2f {
  // World units are square in display pixels. The canvas may be wide, but a
  // circle in this coordinate system stays a circle instead of becoming an oval.
  return (pos - vec2f(0.5)) * vec2f(u.aspect, 1.0);
}

fn from_world(pos: vec2f) -> vec2f {
  return vec2f(0.5) + pos / vec2f(u.aspect, 1.0);
}

fn lane_spacing() -> f32 {
  return min(1.05, u.aspect * 0.28);
}

fn organism_scale() -> f32 {
  return min(1.0, max(0.48, u.aspect * 0.50));
}

fn tangle_spacing() -> f32 {
  return min(0.78, u.aspect * 0.22);
}

fn sense(pos: vec2f, angle: f32) -> f32 {
  let uv = clamp(pos + sensor_offset(angle, 0.022), vec2f(0.002), vec2f(0.998));
  let sample = textureSampleLevel(trail_texture, trail_sampler, uv, 0.0);
  return dot(sample.rgb, vec3f(0.28, 0.50, 0.22));
}

@compute @workgroup_size(${WORKGROUP_SIZE})
fn main(@builtin(global_invocation_id) invocation: vec3u) {
  let index = invocation.x;
  if (index >= arrayLength(&source_particles)) { return; }

  var particle = source_particles[index];
  let variant = u32(clamp(u.variant, 0.0, 3.0) + 0.5);
  var sensor_spread = 0.62;
  var sensor_strength = 0.145;
  var jitter_strength = 0.072;
  if (variant == 1u) {
    sensor_spread = 0.48;
    sensor_strength = 0.18;
    jitter_strength = 0.09;
  } else if (variant == 2u) {
    sensor_spread = 0.38;
    sensor_strength = 0.095;
    jitter_strength = 0.045;
  } else if (variant == 3u) {
    sensor_spread = 0.70;
    sensor_strength = 0.12;
    jitter_strength = 0.055;
  }

  let forward = sense(particle.pos, particle.heading);
  let left = sense(particle.pos, particle.heading - sensor_spread);
  let right = sense(particle.pos, particle.heading + sensor_spread);
  let jitter = (hash(f32(index) + floor(u.time * 15.0)) - 0.5) * jitter_strength;

  var sensor_turn = 0.0;
  if (left > forward || right > forward) {
    sensor_turn = select(-sensor_strength, sensor_strength, right > left);
  }

  let world = to_world(particle.pos);
  let lane_x = (particle.lane - 1.0) * lane_spacing();
  var local_center = vec2f(
    lane_x,
    sin(u.time * 0.17 + particle.lane * 1.7) * 0.018
  );
  if (variant == 1u) {
    let side = select(-1.0, 1.0, world.x >= 0.0);
    local_center = vec2f(side * tangle_spacing(), sin(u.time * 0.21 + side) * 0.025);
  } else if (variant == 2u) {
    local_center = vec2f(
      lane_x,
      -0.055 + abs(particle.lane - 1.0) * 0.025
        + sin(u.time * 0.19 + particle.lane * 1.7) * 0.012
    );
  } else if (variant == 3u) {
    local_center = vec2f(lane_x, sin(u.time * 0.13 + particle.lane * 2.1) * 0.035);
  }
  let centered = world - local_center;
  let radius = max(length(centered), 0.0001);
  let normal = centered / radius;
  let phase = atan2(centered.y, centered.x);
  let lobe = sin(phase * 3.0 - u.time * 0.31)
    + 0.46 * sin(phase * 5.0 + u.time * 0.19);
  let target_radius = (0.245 + lobe * 0.038) * organism_scale();
  let curl = vec2f(-normal.y, normal.x) * (0.48 + 0.20 * sin(phase * 2.0 + u.time * 0.27));
  let crosswind = vec2f(
    sin(centered.y * 18.0 + u.time * 0.37 + particle.noise.x * 1.7),
    cos(centered.x * 21.0 - u.time * 0.29 + particle.noise.y * 1.3)
  ) * 0.13;
  var flow = curl + normal * (target_radius - radius) * 1.8 + crosswind;
  var attractor_limit = 0.032;

  // Tangle: two counter-rotating lobes keep braiding through the center.
  if (variant == 1u) {
    let side = select(-1.0, 1.0, world.x >= 0.0);
    let lobe_radius = radius;
    let lobe_normal = normal;
    let braid = vec2f(
      sin(centered.y * 26.0 + u.time * 0.50),
      cos(centered.x * 24.0 - u.time * 0.36)
    ) * 0.09;
    flow = vec2f(-lobe_normal.y, lobe_normal.x) * side * 0.64
      + lobe_normal * (0.225 * organism_scale() - lobe_radius) * 2.7
      + vec2f(-centered.x * 0.18, -centered.y * 0.07)
      + braid;
    attractor_limit = 0.043;
  }

  // Medusa: three pulsing bells feed soft, downward streaming tentacles.
  if (variant == 2u) {
    let bell_pos = centered;
    let bell_scaled = bell_pos * vec2f(0.92, 1.28);
    let bell_radius = max(length(bell_scaled), 0.0001);
    let bell_normal = bell_scaled / bell_radius;
    let bell_flow = vec2f(-bell_normal.y, bell_normal.x) * 0.42
      + bell_normal * (0.13 + sin(u.time * 0.74 + particle.lane) * 0.010 - bell_radius) * 3.0;
    let tentacle_flow = vec2f(
      sin(bell_pos.y * 34.0 + u.time * 0.82 + particle.noise.x * TAU) * 0.15
        - bell_pos.x * 1.45,
      0.64 + cos(bell_pos.x * 37.0 - u.time * 0.37) * 0.09
    );
    flow = mix(bell_flow, tentacle_flow, smoothstep(-0.015, 0.065, bell_pos.y));
    attractor_limit = 0.038;
  }

  // Starcrossed: a slow five-lobed orbit with a restless crystalline edge.
  if (variant == 3u) {
    let star_radius = (0.205 + 0.060 * cos(phase * 5.0 - u.time * 0.22)) * organism_scale();
    let star_tangent = vec2f(-normal.y, normal.x)
      * (0.31 + 0.11 * sin(phase * 5.0 + u.time * 0.31));
    let facet = vec2f(
      cos(phase * 4.0 + u.time * 0.17),
      sin(phase * 6.0 - u.time * 0.13)
    ) * 0.07;
    flow = star_tangent + normal * (star_radius - radius) * 3.25 + facet;
    attractor_limit = 0.040;
  }
  let desired = atan2(flow.y, flow.x);
  let attractor_turn = clamp(wrap_angle(desired - particle.heading), -attractor_limit, attractor_limit);

  particle.heading = wrap_angle(
    particle.heading + sensor_turn + attractor_turn + jitter
      + 0.021 * sin(u.time * 0.71 + particle.noise.y * TAU)
  );
  particle.pos += sensor_offset(particle.heading, particle.speed * clamp(u.delta, 0.001, 0.05));

  let next_world = to_world(particle.pos);
  if (
    abs(next_world.x) > u.aspect * 0.495
    || abs(next_world.y) > 0.48
    || any(particle.pos < vec2f(0.002))
    || any(particle.pos > vec2f(0.998))
  ) {
    if (variant == 2u) {
      let reset_center = vec2f((particle.lane - 1.0) * lane_spacing(), -0.055);
      particle.pos = from_world(reset_center + vec2f(
        (hash(f32(index) * 1.713 + u.time * 0.07) - 0.5) * 0.18 * organism_scale(),
        -0.20 * organism_scale() + hash(f32(index) * 0.937 + 8.1) * 0.10
      ));
      particle.heading = PI * 0.5 + (hash(f32(index) * 2.117 + u.time * 0.11) - 0.5) * 0.6;
    } else if (variant == 1u) {
      let reset_angle = TAU * hash(f32(index) * 1.713 + u.time * 0.07);
      let reset_radius = 0.05 + 0.23 * sqrt(hash(f32(index) * 0.937 + 8.1));
      let reset_side = select(-1.0, 1.0, hash(f32(index) * 1.411 + 3.7) > 0.5);
      particle.pos = from_world(
        vec2f(reset_side * tangle_spacing(), 0.0)
          + vec2f(cos(reset_angle), sin(reset_angle)) * reset_radius * organism_scale()
      );
      particle.heading = reset_angle + reset_side * PI * 0.5;
    } else if (variant == 3u) {
      let arm = floor(hash(f32(index) * 1.713 + u.time * 0.07) * 5.0);
      let reset_angle = arm * TAU / 5.0
        + (hash(f32(index) * 0.937 + 8.1) - 0.5) * 0.34;
      let reset_radius = (0.05 + 0.25 * sqrt(hash(f32(index) * 2.117 + 4.2))) * organism_scale();
      let reset_center = vec2f((particle.lane - 1.0) * lane_spacing(), 0.0);
      particle.pos = from_world(reset_center + vec2f(cos(reset_angle), sin(reset_angle)) * reset_radius);
      particle.heading = reset_angle + PI * 0.5;
    } else {
      let reset_angle = TAU * hash(f32(index) * 1.713 + u.time * 0.07);
      let reset_radius = (0.07 + 0.25 * sqrt(hash(f32(index) * 0.937 + 8.1))) * organism_scale();
      let reset_center = vec2f((particle.lane - 1.0) * lane_spacing(), 0.0);
      particle.pos = from_world(reset_center + vec2f(cos(reset_angle), sin(reset_angle)) * reset_radius);
      particle.heading = TAU * hash(f32(index) * 2.117 + u.time * 0.11);
    }
  }

  target_particles[index] = particle;
}
`;

  const fadeShader = /* wgsl */ `
struct VertexOut {
  @builtin(position) position: vec4f,
  @location(0) uv: vec2f,
}

@group(0) @binding(0) var source_texture: texture_2d<f32>;
@group(0) @binding(1) var source_sampler: sampler;

@vertex
fn vertex_main(@builtin(vertex_index) vertex_index: u32) -> VertexOut {
  let position = array<vec2f, 3>(
    vec2f(-1.0, -1.0), vec2f(3.0, -1.0), vec2f(-1.0, 3.0)
  )[vertex_index];
  var out: VertexOut;
  out.position = vec4f(position, 0.0, 1.0);
  out.uv = position * 0.5 + vec2f(0.5);
  return out;
}

@fragment
fn fragment_main(in: VertexOut) -> @location(0) vec4f {
  let dimensions = vec2f(textureDimensions(source_texture));
  let texel = 1.0 / max(dimensions, vec2f(1.0));
  let center = textureSample(source_texture, source_sampler, in.uv) * 0.72;
  let blur = (
    textureSample(source_texture, source_sampler, in.uv + vec2f(texel.x, 0.0))
    + textureSample(source_texture, source_sampler, in.uv - vec2f(texel.x, 0.0))
    + textureSample(source_texture, source_sampler, in.uv + vec2f(0.0, texel.y))
    + textureSample(source_texture, source_sampler, in.uv - vec2f(0.0, texel.y))
  ) * 0.07;
  return (center + blur) * vec4f(0.955, 0.951, 0.948, 0.94);
}
`;

  const particleShader = /* wgsl */ `
struct Particle {
  pos: vec2f,
  heading: f32,
  speed: f32,
  noise: vec2f,
  tone: f32,
  lane: f32,
}

struct SimUniforms {
  time: f32,
  delta: f32,
  width: f32,
  height: f32,
  seed: f32,
  aspect: f32,
  point_size: f32,
  variant: f32,
}

struct VertexOut {
  @builtin(position) position: vec4f,
  @location(0) local: vec2f,
  @location(1) color: vec3f,
  @location(2) alpha: f32,
}

@group(0) @binding(0) var<storage, read> particles: array<Particle>;
@group(0) @binding(1) var<uniform> u: SimUniforms;

const TAU: f32 = 6.28318530718;

fn wrap_angle(value: f32) -> f32 {
  return atan2(sin(value), cos(value));
}

@vertex
fn vertex_main(
  @builtin(vertex_index) vertex_index: u32,
  @builtin(instance_index) instance_index: u32,
) -> VertexOut {
  let corners = array<vec2f, 6>(
    vec2f(-1.0, -1.0), vec2f(1.0, -1.0), vec2f(-1.0, 1.0),
    vec2f(-1.0, 1.0), vec2f(1.0, -1.0), vec2f(1.0, 1.0)
  );
  let particle = particles[instance_index];
  let local = corners[vertex_index];
  let pulse = 0.5 + 0.5 * sin(u.time * 0.82 + particle.noise.x * TAU);
  let point_size = u.point_size * (0.72 + particle.noise.y * 0.62 + pulse * 0.16);
  let offset = local * point_size / vec2f(max(u.width, 1.0), max(u.height, 1.0));
  let clip = (particle.pos + offset) * vec2f(2.0, -2.0) + vec2f(-1.0, 1.0);

  let jade = vec3f(0.408, 0.780, 0.698);
  let amber = vec3f(0.961, 0.706, 0.275);
  let bone = vec3f(0.910, 0.867, 0.800);
  let warm = smoothstep(0.58, 0.96, particle.tone + pulse * 0.16);
  let color = mix(mix(jade, bone, 0.28 + pulse * 0.18), amber, warm * 0.78);

  var gap_alpha = 1.0;
  if (u.variant < 0.5) {
    let world = (particle.pos - vec2f(0.5)) * vec2f(u.aspect, 1.0);
    let spacing = min(1.05, u.aspect * 0.28);
    let center = vec2f(
      (particle.lane - 1.0) * spacing,
      sin(u.time * 0.17 + particle.lane * 1.7) * 0.018
    );
    let centered = world - center;
    let angle = atan2(centered.y, centered.x);
    let gap_center = 0.08 + sin(u.time * 0.17) * 0.24;
    gap_alpha = smoothstep(0.16, 0.47, abs(wrap_angle(angle - gap_center)));
  }

  var out: VertexOut;
  out.position = vec4f(clip, 0.0, 1.0);
  out.local = local;
  out.color = color;
  out.alpha = gap_alpha * (0.62 + pulse * 0.38);
  return out;
}

@fragment
fn fragment_main(in: VertexOut) -> @location(0) vec4f {
  let distance = length(in.local);
  if (distance > 1.0) { discard; }
  let soft = smoothstep(1.0, 0.08, distance);
  let strength = soft * in.alpha * 0.028;
  return vec4f(in.color * strength, strength);
}
`;

  const presentShader = /* wgsl */ `
struct VertexOut {
  @builtin(position) position: vec4f,
  @location(0) uv: vec2f,
}

@group(0) @binding(0) var source_texture: texture_2d<f32>;
@group(0) @binding(1) var source_sampler: sampler;

@vertex
fn vertex_main(@builtin(vertex_index) vertex_index: u32) -> VertexOut {
  let position = array<vec2f, 3>(
    vec2f(-1.0, -1.0), vec2f(3.0, -1.0), vec2f(-1.0, 3.0)
  )[vertex_index];
  var out: VertexOut;
  out.position = vec4f(position, 0.0, 1.0);
  out.uv = position * 0.5 + vec2f(0.5);
  return out;
}

@fragment
fn fragment_main(in: VertexOut) -> @location(0) vec4f {
  var color = textureSample(source_texture, source_sampler, in.uv).rgb;
  color = vec3f(1.0) - exp(-color * 2.15);
  let edge = 1.0 - smoothstep(0.18, 0.78, length((in.uv - vec2f(0.5)) * vec2f(1.0, 0.72)));
  color *= 0.76 + edge * 0.24;
  let alpha = clamp(max(color.r, max(color.g, color.b)) * 1.08, 0.0, 0.96);
  return vec4f(min(color, vec3f(alpha)), alpha);
}
`;

  function rendererMode() {
    const requested = new URLSearchParams(window.location.search).get("thinking");
    if (["auto", "fluid", "classic"].includes(requested)) return requested;
    try {
      const stored = window.localStorage.getItem(MODE_STORAGE_KEY);
      if (["auto", "fluid", "classic"].includes(stored)) return stored;
    } catch {
      // Storage is optional; auto remains the safe default.
    }
    return "auto";
  }

  function requestedVisual() {
    const requested = new URLSearchParams(window.location.search).get("thinkingViz")?.toLowerCase();
    return VISUALS.includes(requested) ? requested : null;
  }

  function prefersReducedMotion() {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
  }

  function modalObscuresChat() {
    return Boolean(document.querySelector("dialog[open]"));
  }

  function hashSeed(value) {
    let hash = 2166136261;
    for (const char of String(value || "calliope")) {
      hash ^= char.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function seededRandom(seed) {
    let state = seed || 0x6d2b79f5;
    return () => {
      state += 0x6d2b79f5;
      let value = state;
      value = Math.imul(value ^ (value >>> 15), value | 1);
      value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
      return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
    };
  }

  function timeout(promise, ms) {
    return Promise.race([
      promise,
      new Promise((_, reject) => {
        window.setTimeout(() => reject(new Error("WebGPU startup timed out")), ms);
      }),
    ]);
  }

  async function checkedModule(device, label, code) {
    const module = device.createShaderModule({ label, code });
    if (typeof module.getCompilationInfo === "function") {
      const info = await module.getCompilationInfo();
      const errors = info.messages.filter((message) => message.type === "error");
      if (errors.length) {
        throw new Error(`${label}: ${errors.map((message) => message.message).join("; ")}`);
      }
    }
    return module;
  }

  async function createPipeline(device, method, descriptor) {
    const asyncMethod = `${method}Async`;
    if (typeof device[asyncMethod] === "function") return device[asyncMethod](descriptor);
    return device[method](descriptor);
  }

  async function acquireRuntime() {
    if (lostRuntime || !window.isSecureContext || !("gpu" in navigator) || !navigator.gpu) {
      throw new Error("WebGPU is unavailable in this browser context");
    }
    let adapter = await navigator.gpu.requestAdapter({ powerPreference: "low-power" });
    if (!adapter) adapter = await navigator.gpu.requestAdapter();
    if (!adapter) throw new Error("No compatible WebGPU adapter");
    const particleBytes = PARTICLE_COUNT * PARTICLE_STRIDE;
    if (
      adapter.limits.maxStorageBuffersPerShaderStage < 2
      || adapter.limits.maxStorageBufferBindingSize < particleBytes
    ) {
      throw new Error("The WebGPU adapter cannot render storage-buffer particles");
    }
    const device = await adapter.requestDevice();
    const canvasFormat = navigator.gpu.getPreferredCanvasFormat();
    const [computeModule, fadeModule, particleModule, presentModule] = await Promise.all([
      checkedModule(device, "calliope-fluid-compute", computeShader),
      checkedModule(device, "calliope-fluid-fade", fadeShader),
      checkedModule(device, "calliope-fluid-particles", particleShader),
      checkedModule(device, "calliope-fluid-present", presentShader),
    ]);
    const [computePipeline, fadePipeline, particlePipeline, presentPipeline] = await Promise.all([
      createPipeline(device, "createComputePipeline", {
        label: "calliope-fluid-compute",
        layout: "auto",
        compute: { module: computeModule, entryPoint: "main" },
      }),
      createPipeline(device, "createRenderPipeline", {
        label: "calliope-fluid-fade",
        layout: "auto",
        vertex: { module: fadeModule, entryPoint: "vertex_main" },
        fragment: { module: fadeModule, entryPoint: "fragment_main", targets: [{ format: TRAIL_FORMAT }] },
        primitive: { topology: "triangle-list" },
      }),
      createPipeline(device, "createRenderPipeline", {
        label: "calliope-fluid-particles",
        layout: "auto",
        vertex: { module: particleModule, entryPoint: "vertex_main" },
        fragment: {
          module: particleModule,
          entryPoint: "fragment_main",
          targets: [{
            format: TRAIL_FORMAT,
            blend: {
              color: { srcFactor: "one", dstFactor: "one", operation: "add" },
              alpha: { srcFactor: "one", dstFactor: "one", operation: "add" },
            },
          }],
        },
        primitive: { topology: "triangle-list" },
      }),
      createPipeline(device, "createRenderPipeline", {
        label: "calliope-fluid-present",
        layout: "auto",
        vertex: { module: presentModule, entryPoint: "vertex_main" },
        fragment: { module: presentModule, entryPoint: "fragment_main", targets: [{ format: canvasFormat }] },
        primitive: { topology: "triangle-list" },
      }),
    ]);
    const sampler = device.createSampler({
      label: "calliope-fluid-linear",
      magFilter: "linear",
      minFilter: "linear",
      addressModeU: "clamp-to-edge",
      addressModeV: "clamp-to-edge",
    });
    const runtime = {
      device, canvasFormat, sampler,
      computePipeline, fadePipeline, particlePipeline, presentPipeline,
    };
    void device.lost.then((info) => {
      lostRuntime = true;
      runtimePromise = null;
      for (const controller of [...live]) {
        controller.fail(`GPU device lost${info.message ? `: ${info.message}` : ""}`);
      }
    });
    return runtime;
  }

  function getRuntime() {
    if (!runtimePromise) runtimePromise = acquireRuntime();
    return runtimePromise;
  }

  function median(values) {
    const ordered = [...values].sort((left, right) => left - right);
    return ordered[Math.floor(ordered.length / 2)] || 0;
  }

  class FluidController {
    constructor(canvas, mode) {
      this.canvas = canvas;
      this.rayCanvas = canvas.closest(".thinking-indicator")?.querySelector("canvas[data-calliope-ray]") || null;
      this.mode = mode;
      this.seed = hashSeed(canvas.dataset.fluodditySeed);
      const forcedVisual = requestedVisual();
      this.visualLocked = Boolean(forcedVisual);
      this.visual = forcedVisual || VISUALS[this.seed % VISUALS.length];
      this.variant = VISUALS.indexOf(this.visual);
      this.visualStep = (this.seed & 1) ? 1 : VISUALS.length - 1;
      this.canvas.dataset.fluoddityVisual = this.visual;
      this.runtime = null;
      this.context = null;
      this.buffers = [];
      this.trails = [];
      this.computeGroups = [];
      this.fadeGroups = [];
      this.particleGroups = [];
      this.presentGroups = [];
      this.uniformBuffer = null;
      this.particleIndex = 0;
      this.trailIndex = 0;
      this.width = 0;
      this.height = 0;
      this.lastDrawAt = 0;
      this.lastEvolutionAt = 0;
      this.nextDrawAt = 0;
      this.frame = 0;
      this.visible = true;
      this.ready = false;
      this.started = false;
      this.stopped = false;
      this.backlogProbe = false;
      this.probeObscured = false;
      this.lastBacklogProbeAt = 0;
      this.slowBacklogs = 0;
      this.fastBacklogs = 0;
      this.frameInterval = FRAME_INTERVAL;
      this.modalEpoch = 0;
      this.resizeObserver = null;
      this.intersectionObserver = null;
      this.modalObserver = null;
      this.handleVisibility = () => this.sync();
      this.tick = (now) => this.onFrame(now);
      window.CalliopeParametricRays?.unmount(this.rayCanvas);
      this.canvas.closest(".thinking-indicator")?.classList.add("fluid-probing");
    }

    installObservers() {
      if ("ResizeObserver" in window) {
        this.resizeObserver = new ResizeObserver(() => {
          if (this.ready) this.resize();
        });
        this.resizeObserver.observe(this.canvas);
      }
      if ("IntersectionObserver" in window) {
        this.intersectionObserver = new IntersectionObserver(([entry]) => {
          this.visible = entry?.isIntersecting !== false;
          this.sync();
        });
        this.intersectionObserver.observe(this.canvas);
      }
      if ("MutationObserver" in window) {
        this.modalObserver = new MutationObserver(() => {
          this.modalEpoch += 1;
          if (this.started && !this.ready && modalObscuresChat()) this.probeObscured = true;
          this.sync();
        });
        this.modalObserver.observe(document.documentElement, {
          attributes: true,
          attributeFilter: ["open"],
          subtree: true,
        });
      }
      document.addEventListener("visibilitychange", this.handleVisibility);
    }

    async start() {
      if (this.started || this.stopped || modalObscuresChat()) return;
      this.started = true;
      this.canvas.dataset.fluoddityState = "probing";
      lastDecision = { mode: this.mode, state: "probing", reason: "", probe_ms: null, visual: this.visual };
      try {
        this.runtime = await timeout(getRuntime(), STARTUP_TIMEOUT_MS);
        if (this.stopped || !this.canvas.isConnected) return;
        this.context = this.canvas.getContext("webgpu");
        if (!this.context) throw new Error("This canvas cannot create a WebGPU context");
        this.createResources();
        this.resize();
        this.runtime.device.pushErrorScope("validation");
        this.encodeFrame(performance.now(), 1 / TARGET_FPS);
        await this.runtime.device.queue.onSubmittedWorkDone();
        const samples = [];
        for (let index = 0; index < 3; index += 1) {
          const began = performance.now();
          this.encodeFrame(performance.now(), 1 / TARGET_FPS);
          await this.runtime.device.queue.onSubmittedWorkDone();
          samples.push(performance.now() - began);
        }
        const validationError = await this.runtime.device.popErrorScope();
        if (validationError) throw validationError;
        const probeMs = median(samples);
        let initialState = "ready";
        let initialReason = "";
        if (this.mode === "auto" && !this.probeObscured && !modalObscuresChat()) {
          if (probeMs > AUTO_FALLBACK_BUDGET_MS) {
            throw new Error(`GPU probe took ${probeMs.toFixed(1)} ms`);
          }
          if (probeMs > AUTO_PROBE_BUDGET_MS) {
            this.frameInterval = THROTTLED_FRAME_INTERVAL;
            initialState = "throttled";
            initialReason = `GPU probe pressure; rendering at ${THROTTLED_FPS} fps`;
          }
        }
        if (this.stopped || !this.canvas.isConnected) return;
        this.ready = true;
        this.lastEvolutionAt = performance.now();
        this.canvas.dataset.fluoddityState = initialState;
        this.canvas.dataset.fluoddityProbeMs = probeMs.toFixed(2);
        if (initialState === "throttled") this.canvas.dataset.fluoddityBacklogMs = probeMs.toFixed(1);
        const indicator = this.canvas.closest(".thinking-indicator");
        indicator?.classList.remove("fluid-probing");
        indicator?.classList.add("fluid-ready");
        lastDecision = {
          mode: this.mode,
          state: initialState,
          reason: initialReason,
          probe_ms: probeMs,
          visual: this.visual,
        };
        this.sync();
      } catch (error) {
        this.fail(error instanceof Error ? error.message : String(error));
      }
    }

    createResources() {
      const { device } = this.runtime;
      const random = seededRandom(this.seed);
      const bounds = this.canvas.getBoundingClientRect();
      const aspect = Math.max(0.75, bounds.width / Math.max(1, bounds.height));
      const laneSpacing = Math.min(1.05, aspect * 0.28);
      const organismScale = Math.min(1, Math.max(0.48, aspect * 0.5));
      const tangleSpacing = Math.min(0.78, aspect * 0.22);
      const particles = new Float32Array(PARTICLE_COUNT * PARTICLE_FLOATS);
      for (let index = 0; index < PARTICLE_COUNT; index += 1) {
        const base = index * PARTICLE_FLOATS;
        const lane = Math.floor(random() * 3);
        const laneCenter = (lane - 1) * laneSpacing;
        if (this.visual === "tangle") {
          const side = random() < 0.5 ? -1 : 1;
          const angle = random() * Math.PI * 2;
          const radius = Math.sqrt(random()) * 0.25 * organismScale;
          particles[base] = 0.5 + (side * tangleSpacing + Math.cos(angle) * radius) / aspect;
          particles[base + 1] = 0.5 + Math.sin(angle) * radius;
          particles[base + 2] = angle + side * Math.PI * 0.5;
        } else if (this.visual === "medusa") {
          if (random() < 0.68) {
            const angle = Math.PI + random() * Math.PI;
            const radius = (0.055 + Math.sqrt(random()) * 0.115) * organismScale;
            particles[base] = 0.5 + (laneCenter + Math.cos(angle) * radius) / aspect;
            particles[base + 1] = 0.445 + Math.abs(lane - 1) * 0.025 + Math.sin(angle) * radius * 0.72;
            particles[base + 2] = angle + Math.PI * 0.5;
          } else {
            particles[base] = 0.5 + (laneCenter + (random() - 0.5) * 0.15 * organismScale) / aspect;
            particles[base + 1] = 0.445 + Math.abs(lane - 1) * 0.025 + random() * 0.26 * organismScale;
            particles[base + 2] = Math.PI * 0.5 + (random() - 0.5) * 0.35;
          }
        } else if (this.visual === "starcrossed") {
          const arm = Math.floor(random() * 5);
          const angle = arm * Math.PI * 2 / 5 + (random() - 0.5) * 0.32;
          const radius = (0.05 + Math.sqrt(random()) * 0.25) * organismScale;
          particles[base] = 0.5 + (laneCenter + Math.cos(angle) * radius) / aspect;
          particles[base + 1] = 0.5 + Math.sin(angle) * radius;
          particles[base + 2] = angle + Math.PI * 0.5;
        } else {
          const angle = random() * Math.PI * 2;
          const radius = Math.sqrt(random()) * 0.30 * organismScale
            * (0.88 + Math.sin(angle * 3 + this.seed * 0.0001) * 0.12);
          particles[base] = 0.5 + (laneCenter + Math.cos(angle) * radius) / aspect;
          particles[base + 1] = 0.5 + Math.sin(angle) * radius;
          particles[base + 2] = random() * Math.PI * 2;
        }
        particles[base + 3] = 0.047 + random() * 0.035;
        particles[base + 4] = random();
        particles[base + 5] = random();
        particles[base + 6] = random();
        particles[base + 7] = lane;
      }
      this.buffers = [0, 1].map((index) => {
        const buffer = device.createBuffer({
          label: `calliope-fluid-particles-${index}`,
          size: PARTICLE_COUNT * PARTICLE_STRIDE,
          usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
        });
        device.queue.writeBuffer(buffer, 0, particles);
        return buffer;
      });
      this.uniformBuffer = device.createBuffer({
        label: "calliope-fluid-uniforms",
        size: 32,
        usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
      });
    }

    resize() {
      if (!this.runtime || !this.context || this.stopped) return;
      const bounds = this.canvas.getBoundingClientRect();
      if (bounds.width < 2 || bounds.height < 2) return;
      const pixelRatio = Math.min(
        window.devicePixelRatio || 1,
        PIXEL_RATIO_CAP,
        Math.max(1, TRAIL_WIDTH_CAP / bounds.width),
      );
      const width = Math.max(2, Math.round(bounds.width * pixelRatio));
      const height = Math.max(2, Math.round(bounds.height * pixelRatio));
      if (width === this.width && height === this.height && this.trails.length) return;
      this.width = width;
      this.height = height;
      this.canvas.width = width;
      this.canvas.height = height;
      this.context.configure({
        device: this.runtime.device,
        format: this.runtime.canvasFormat,
        alphaMode: "premultiplied",
      });
      for (const trail of this.trails) trail.destroy();
      this.trails = [0, 1].map((index) => this.runtime.device.createTexture({
        label: `calliope-fluid-trail-${index}`,
        size: { width, height },
        format: TRAIL_FORMAT,
        usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING,
      }));
      this.rebuildGroups();
      // Particle/trail bind groups advance in lockstep. A resize replaces the
      // trail textures, so restart both ping-pong indices from the same side.
      this.particleIndex = 0;
      this.trailIndex = 0;
    }

    rebuildGroups() {
      const runtime = this.runtime;
      const views = this.trails.map((trail) => trail.createView());
      this.computeGroups = [0, 1].map((index) => runtime.device.createBindGroup({
        label: `calliope-fluid-compute-group-${index}`,
        layout: runtime.computePipeline.getBindGroupLayout(0),
        entries: [
          { binding: 0, resource: { buffer: this.buffers[index] } },
          { binding: 1, resource: { buffer: this.buffers[1 - index] } },
          { binding: 2, resource: views[index] },
          { binding: 3, resource: runtime.sampler },
          { binding: 4, resource: { buffer: this.uniformBuffer } },
        ],
      }));
      this.fadeGroups = [0, 1].map((index) => runtime.device.createBindGroup({
        label: `calliope-fluid-fade-group-${index}`,
        layout: runtime.fadePipeline.getBindGroupLayout(0),
        entries: [
          { binding: 0, resource: views[index] },
          { binding: 1, resource: runtime.sampler },
        ],
      }));
      this.particleGroups = [0, 1].map((index) => runtime.device.createBindGroup({
        label: `calliope-fluid-particle-group-${index}`,
        layout: runtime.particlePipeline.getBindGroupLayout(0),
        entries: [
          { binding: 0, resource: { buffer: this.buffers[index] } },
          { binding: 1, resource: { buffer: this.uniformBuffer } },
        ],
      }));
      this.presentGroups = [0, 1].map((index) => runtime.device.createBindGroup({
        label: `calliope-fluid-present-group-${index}`,
        layout: runtime.presentPipeline.getBindGroupLayout(0),
        entries: [
          { binding: 0, resource: views[index] },
          { binding: 1, resource: runtime.sampler },
        ],
      }));
    }

    encodeFrame(now, forcedDelta = null) {
      if (!this.runtime || !this.context || !this.trails.length || this.stopped) return;
      const delta = forcedDelta ?? Math.min(0.05, Math.max(0.001, (now - this.lastDrawAt) / 1000 || 1 / TARGET_FPS));
      const aspect = this.width / Math.max(1, this.height);
      const uniform = new Float32Array([
        now / 1000,
        delta,
        this.width,
        this.height,
        this.seed,
        aspect,
        Math.max(0.78, Math.min(1.45, this.height / 120)),
        this.variant,
      ]);
      this.runtime.device.queue.writeBuffer(this.uniformBuffer, 0, uniform);
      const source = this.trailIndex;
      const target = 1 - source;
      const particleTarget = 1 - this.particleIndex;
      const encoder = this.runtime.device.createCommandEncoder({ label: "calliope-fluid-frame" });

      const compute = encoder.beginComputePass({ label: "calliope-fluid-update" });
      compute.setPipeline(this.runtime.computePipeline);
      compute.setBindGroup(0, this.computeGroups[this.particleIndex]);
      compute.dispatchWorkgroups(Math.ceil(PARTICLE_COUNT / WORKGROUP_SIZE));
      compute.end();

      const fade = encoder.beginRenderPass({
        label: "calliope-fluid-fade",
        colorAttachments: [{
          view: this.trails[target].createView(),
          loadOp: "clear",
          storeOp: "store",
          clearValue: { r: 0, g: 0, b: 0, a: 0 },
        }],
      });
      fade.setPipeline(this.runtime.fadePipeline);
      fade.setBindGroup(0, this.fadeGroups[source]);
      fade.draw(3);
      fade.end();

      const particles = encoder.beginRenderPass({
        label: "calliope-fluid-particles",
        colorAttachments: [{
          view: this.trails[target].createView(),
          loadOp: "load",
          storeOp: "store",
        }],
      });
      particles.setPipeline(this.runtime.particlePipeline);
      particles.setBindGroup(0, this.particleGroups[particleTarget]);
      particles.draw(6, PARTICLE_COUNT);
      particles.end();

      const present = encoder.beginRenderPass({
        label: "calliope-fluid-present",
        colorAttachments: [{
          view: this.context.getCurrentTexture().createView(),
          loadOp: "clear",
          storeOp: "store",
          clearValue: { r: 0, g: 0, b: 0, a: 0 },
        }],
      });
      present.setPipeline(this.runtime.presentPipeline);
      present.setBindGroup(0, this.presentGroups[target]);
      present.draw(3);
      present.end();

      this.runtime.device.queue.submit([encoder.finish()]);
      this.particleIndex = particleTarget;
      this.trailIndex = target;
      this.lastDrawAt = now;
    }

    onFrame(now) {
      this.frame = 0;
      if (this.stopped || !this.canvas.isConnected) return this.stop();
      if (!this.ready || !this.visible || document.visibilityState !== "visible" || modalObscuresChat()) {
        return this.sync();
      }
      if (!this.visualLocked && now - this.lastEvolutionAt >= EVOLUTION_INTERVAL_MS) {
        this.evolveVisual(now);
      }
      if (!this.nextDrawAt || now >= this.nextDrawAt) {
        this.nextDrawAt = now + this.frameInterval - 1;
        this.resize();
        this.encodeFrame(now);
        if (!this.backlogProbe && now - this.lastBacklogProbeAt >= 4000) this.probeBacklog();
      }
      this.frame = window.requestAnimationFrame(this.tick);
    }

    evolveVisual(now) {
      this.variant = (this.variant + this.visualStep) % VISUALS.length;
      this.visual = VISUALS[this.variant];
      this.lastEvolutionAt = now;
      this.canvas.dataset.fluoddityVisual = this.visual;
      lastDecision = { ...lastDecision, visual: this.visual };
    }

    probeBacklog() {
      if (!this.runtime || this.backlogProbe || this.stopped || modalObscuresChat()) return;
      this.backlogProbe = true;
      this.lastBacklogProbeAt = performance.now();
      const began = performance.now();
      const modalEpoch = this.modalEpoch;
      void this.runtime.device.queue.onSubmittedWorkDone().then(() => {
        this.backlogProbe = false;
        if (this.stopped) return;
        if (
          modalEpoch !== this.modalEpoch
          || modalObscuresChat()
          || document.visibilityState !== "visible"
          || !this.visible
        ) {
          this.slowBacklogs = 0;
          this.fastBacklogs = 0;
          return;
        }
        const elapsed = performance.now() - began;
        if (elapsed > RUNTIME_BACKLOG_BUDGET_MS) {
          this.slowBacklogs += 1;
          this.fastBacklogs = 0;
          if (this.slowBacklogs >= RUNTIME_BACKLOG_LIMIT && this.frameInterval !== THROTTLED_FRAME_INTERVAL) {
            this.frameInterval = THROTTLED_FRAME_INTERVAL;
            this.nextDrawAt = 0;
            this.canvas.dataset.fluoddityState = "throttled";
            this.canvas.dataset.fluoddityBacklogMs = elapsed.toFixed(1);
            lastDecision = {
              mode: this.mode,
              state: "throttled",
              reason: `GPU queue pressure; rendering at ${THROTTLED_FPS} fps`,
              probe_ms: Number(this.canvas.dataset.fluoddityProbeMs || 0),
              visual: this.visual,
            };
          }
        } else {
          this.slowBacklogs = 0;
          this.fastBacklogs += 1;
          if (this.fastBacklogs >= RUNTIME_RECOVERY_LIMIT && this.frameInterval !== FRAME_INTERVAL) {
            this.frameInterval = FRAME_INTERVAL;
            this.nextDrawAt = 0;
            this.canvas.dataset.fluoddityState = "ready";
            delete this.canvas.dataset.fluoddityBacklogMs;
            lastDecision = {
              mode: this.mode,
              state: "ready",
              reason: "",
              probe_ms: Number(this.canvas.dataset.fluoddityProbeMs || 0),
              visual: this.visual,
            };
          }
        }
      }).catch((error) => this.fail(error instanceof Error ? error.message : String(error)));
    }

    sync() {
      if (this.stopped) return;
      const shouldRun = this.visible
        && document.visibilityState === "visible"
        && !modalObscuresChat();
      if (!this.started) {
        if (shouldRun) void this.start();
        return;
      }
      if (!this.ready) return;
      if (shouldRun && !this.frame) this.frame = window.requestAnimationFrame(this.tick);
      if (!shouldRun && this.frame) {
        window.cancelAnimationFrame(this.frame);
        this.frame = 0;
      }
    }

    fail(reason) {
      if (this.stopped) return;
      this.canvas.dataset.fluoddityState = "fallback";
      this.canvas.dataset.fluoddityReason = String(reason || "WebGPU initialization failed").slice(0, 180);
      lastDecision = {
        mode: this.mode,
        state: "fallback",
        reason: this.canvas.dataset.fluoddityReason,
        probe_ms: null,
        visual: this.visual,
      };
      this.stop(false);
      if (this.rayCanvas?.isConnected) {
        window.CalliopeParametricRays?.mount(this.rayCanvas, this.rayCanvas.dataset.calliopeRay);
      }
    }

    stop(clearState = true) {
      if (this.stopped) return;
      this.stopped = true;
      this.ready = false;
      window.cancelAnimationFrame(this.frame);
      this.resizeObserver?.disconnect();
      this.intersectionObserver?.disconnect();
      this.modalObserver?.disconnect();
      document.removeEventListener("visibilitychange", this.handleVisibility);
      const indicator = this.canvas.closest(".thinking-indicator");
      indicator?.classList.remove("fluid-probing");
      indicator?.classList.remove("fluid-ready");
      for (const buffer of this.buffers) buffer.destroy();
      for (const trail of this.trails) trail.destroy();
      this.context?.unconfigure?.();
      this.buffers = [];
      this.trails = [];
      live.delete(this);
      active.delete(this.canvas);
      if (clearState) {
        delete this.canvas.dataset.fluoddityState;
        delete this.canvas.dataset.fluoddityReason;
        delete this.canvas.dataset.fluoddityProbeMs;
        delete this.canvas.dataset.fluoddityBacklogMs;
        delete this.canvas.dataset.fluoddityVisual;
      }
    }
  }

  function mount(canvas) {
    if (!(canvas instanceof HTMLCanvasElement)) return;
    active.get(canvas)?.stop();
    const mode = rendererMode();
    if (mode === "classic" || prefersReducedMotion()) {
      canvas.dataset.fluoddityState = mode === "classic" ? "classic" : "reduced-motion";
      lastDecision = { mode, state: canvas.dataset.fluoddityState, reason: "", probe_ms: null, visual: null };
      const rayCanvas = canvas.closest(".thinking-indicator")?.querySelector("canvas[data-calliope-ray]");
      if (rayCanvas) window.CalliopeParametricRays?.mount(rayCanvas, rayCanvas.dataset.calliopeRay);
      return;
    }
    const controller = new FluidController(canvas, mode);
    active.set(canvas, controller);
    live.add(controller);
    controller.installObservers();
    controller.sync();
  }

  function unmount(canvas) {
    active.get(canvas)?.stop();
  }

  function unmountAll(root = document) {
    root.querySelectorAll("canvas[data-fluoddity-thinking]").forEach(unmount);
  }

  function mountAll(root = document) {
    root.querySelectorAll("canvas[data-fluoddity-thinking]").forEach((canvas) => {
      if (!active.has(canvas) && !canvas.dataset.fluoddityState) mount(canvas);
    });
  }

  function setMode(mode) {
    if (!["auto", "fluid", "classic"].includes(mode)) return false;
    try {
      window.localStorage.setItem(MODE_STORAGE_KEY, mode);
      return true;
    } catch {
      return false;
    }
  }

  window.CalliopeFluoddityThinking = {
    mount,
    unmount,
    mountAll,
    unmountAll,
    setMode,
    mode: rendererMode,
    status: () => ({
      ...lastDecision,
      particle_count: PARTICLE_COUNT,
      target_fps: TARGET_FPS,
      evolution_ms: EVOLUTION_INTERVAL_MS,
      visuals: [...VISUALS],
    }),
  };
})();
