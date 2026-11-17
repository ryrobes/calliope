import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type { ThemeMode } from "./theme";

type RGB = [number, number, number];
const marker = "calliopeDisplayColors";
const fields = ["strokeColor", "backgroundColor", "opacity"] as const;
let context: CanvasRenderingContext2D | null;
const colors = new Map<string, RGB>();

function rgb(value: string): RGB {
  const cached = colors.get(value);
  if (cached) return cached;
  context ||= document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  if (!context) return [128, 128, 128];
  context.clearRect(0, 0, 1, 1);
  context.fillStyle = "#808080";
  context.fillStyle = value;
  context.fillRect(0, 0, 1, 1);
  const data = context.getImageData(0, 0, 1, 1).data;
  const result: RGB = [data[0], data[1], data[2]];
  if (colors.size > 512) colors.clear();
  colors.set(value, result);
  return result;
}
const hex = (color: RGB) => "#" + color.map(v => Math.round(v).toString(16).padStart(2, "0")).join("");
const mix = (a: RGB, b: RGB, amount: number): RGB => a.map((v, i) => v + (b[i] - v) * amount) as RGB;
const luminance = (color: RGB) => color.map(v => {
  const n = v / 255;
  return n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4;
}).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
export const contrast = (a: string, b: string) => {
  const [x, y] = [luminance(rgb(a)), luminance(rgb(b))].sort((a, b) => b - a);
  return (x + .05) / (y + .05);
};

// Blend toward white/black only as far as necessary. This retains hue families
// and avoids Excalidraw's global inversion (which also affects embedded images).
function readable(color: string, background: string, minimum: number) {
  if (contrast(color, background) >= minimum) return color;
  const target: RGB = contrast("#ffffff", background) > contrast("#000000", background)
    ? [255, 255, 255] : [0, 0, 0];
  const source = rgb(color);
  let low = 0, high = 1;
  for (let i = 0; i < 14; i++) {
    const mid = (low + high) / 2;
    if (contrast(hex(mix(source, target, mid)), background) >= minimum) high = mid;
    else low = mid;
  }
  return hex(mix(source, target, high));
}

export function sketchBackground(mode: ThemeMode): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue("--sketch-bg").trim();
  // Resolve var()/color-mix()/oklch() using the browser, not a second CSS parser.
  const probe = document.createElement("span");
  probe.style.color = value || (mode === "dark" ? "#111b24" : "#f5f8f5");
  document.body.append(probe);
  const result = hex(rgb(getComputedStyle(probe).color));
  probe.remove();
  return result;
}

/** Restore authored colors before fingerprinting or saving. Metadata travels
 * with Excalidraw undo/redo and duplicate operations, but never reaches storage.
 * A color explicitly changed by a human takes precedence over the old source. */
export function authoredElements(elements: readonly ExcalidrawElement[]): ExcalidrawElement[] {
  return elements.map(element => {
    const saved = element.customData?.[marker];
    if (!saved || saved.version !== 1) return element;
    const copy = { ...element, customData: { ...element.customData } };
    for (const key of fields) {
      if (element[key] === saved.display[key]) (copy as any)[key] = saved.original[key];
    }
    delete copy.customData[marker];
    if (!saved.hadCustomData && !Object.keys(copy.customData).length) delete (copy as any).customData;
    return copy;
  });
}

export function displayElements(elements: readonly ExcalidrawElement[], background: string): ExcalidrawElement[] {
  const authored = authoredElements(elements);
  const fills = new Map(authored.map(element => [element.id,
    element.backgroundColor === "transparent" ? background
      : hex(mix(rgb(background), rgb(element.backgroundColor), .12))]));
  return authored.map((element, index) => {
    // Never change photos/avatars or intentionally hidden/deleted objects.
    if (element.isDeleted || element.type === "image" || element.opacity === 0) return elements[index];
    const backdrop = element.type === "text" && element.containerId
      ? fills.get(element.containerId) || background : fills.get(element.id) || background;
    const original = Object.fromEntries(fields.map(key => [key, element[key]]));
    const display = {
      strokeColor: element.strokeColor === "transparent" ? "transparent"
        : readable(element.strokeColor, backdrop, element.type === "text" ? 7 : 4.5),
      backgroundColor: element.backgroundColor === "transparent" ? "transparent" : fills.get(element.id)!,
      // Low-opacity labels defeat color contrast; keep their authored opacity
      // for storage while making visible ink legible in the viewer.
      opacity: 100,
    };
    const metadata = { version: 1, original, display, hadCustomData: element.customData !== undefined };
    const current = elements[index];
    if (fields.every(key => current[key] === display[key])
      && JSON.stringify(current.customData?.[marker]) === JSON.stringify(metadata)) return current;
    return { ...element, ...display, customData: { ...element.customData, [marker]: metadata } };
  });
}
