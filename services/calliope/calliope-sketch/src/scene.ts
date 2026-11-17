import { convertToExcalidrawElements, restoreElements } from "@excalidraw/excalidraw";
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import { DEFAULT_SKETCH_FONT } from "./fonts";

export type SketchScene = {
  elements?: Array<Record<string, unknown>>;
  appState?: Record<string, unknown>;
  // Server-managed binary store (Briarpatch avatar thumbnails et al).
  // Saves never send it back; the server column is authoritative.
  files?: Record<string, Record<string, unknown>>;
};

function isNativeElement(element: Record<string, unknown>) {
  return Number.isInteger(element.version)
    && Number.isInteger(element.versionNonce)
    && Number.isInteger(element.seed);
}

function stableLabelId(elementId: unknown) {
  return `${String(elementId || "label").slice(0, 89)}--label`;
}

/** Re-run the same bound-label layout as entering/leaving the text editor.
 * restoreElements({ refreshDimensions: true }) measures text, but does not
 * reposition it against its container. Use the public label constructor on a
 * scratch copy, retaining only layout (not newly generated element metadata).
 */
function layoutBoundLabels(elements: ExcalidrawElement[]): ExcalidrawElement[] {
  const byId = new Map(elements.map((element) => [element.id, element]));
  for (const text of elements) {
    if (text.type !== "text" || !text.containerId) continue;
    const container = byId.get(text.containerId);
    if (!container || (container.type !== "rectangle" && container.type !== "ellipse"
      && container.type !== "diamond" && container.type !== "arrow")) continue;
    // Let the temporary label use the constructor's automatic measurement.
    const { autoResize: _autoResize, ...label } = text;
    const layout = convertToExcalidrawElements([{
      ...container,
      // This is a layout calculation, not another binding or endpoint edit.
      boundElements: [],
      label: { ...label, text: text.originalText ?? text.text },
    }] as Parameters<typeof convertToExcalidrawElements>[0], { regenerateIds: false });
    const laidOutText = layout.find((element) => element.id === text.id);
    const laidOutContainer = layout.find((element) => element.id === container.id);
    if (laidOutText?.type !== "text" || !laidOutContainer) continue;
    // The constructor always measures a new text box. Preserve a saved fixed
    // width, adjusting its alignment anchor against the measured layout.
    const width = text.autoResize === false ? text.width : laidOutText.width;
    const anchor = container.type === "arrow" || text.textAlign === "center"
      ? 0.5 : text.textAlign === "right" ? 1 : 0;
    byId.set(text.id, {
      ...text,
      x: laidOutText.x + (laidOutText.width - width) * anchor, y: laidOutText.y,
      width, height: laidOutText.height,
      text: laidOutText.text, angle: laidOutText.angle,
    });
    byId.set(container.id, {
      ...container,
      width: laidOutContainer.width, height: laidOutContainer.height,
    });
  }
  return elements.map((element) => byId.get(element.id)!);
}

/**
 * Turn Calliope's small semantic DSL into one canonical Excalidraw scene.
 *
 * A scene may be mixed: human-edited elements are already native while newly
 * added Calliope elements are semantic skeletons. Conversion must retain the
 * human work, materialize labels/bindings once, and strip the construction
 * hints so the next mixed revision cannot duplicate them.
 */
export function materializeSketchElements(scene: SketchScene): ExcalidrawElement[] {
  const source = Array.isArray(scene?.elements)
    ? scene.elements.filter((element): element is Record<string, unknown> =>
      Boolean(element && typeof element === "object" && element.isDeleted !== true))
    : [];
  const sourceIds = new Set(source.map((element) => String(element.id || "")));
  const hasBoundText = (element: Record<string, unknown>) =>
    Array.isArray(element.boundElements)
      && element.boundElements.some((bound: any) =>
        bound?.type === "text" && sourceIds.has(String(bound.id || "")));
  const needsMaterialization = source.some((element) =>
    !isNativeElement(element)
      || (Boolean((element.label as any)?.text) && !hasBoundText(element)));

  const skeletons = source.map((element) => {
    const clean: Record<string, any> = { ...element };
    if (clean.type === "text" && !clean.fontFamily && !isNativeElement(element)) {
      clean.fontFamily = DEFAULT_SKETCH_FONT;
    }
    if (Array.isArray(clean.boundElements)) {
      clean.boundElements = clean.boundElements.filter((bound: any) =>
        bound && sourceIds.has(String(bound.id || "")));
    }
    // Native elements may retain the semantic construction hints after a
    // human save. Do not feed those hints back through the converter or it can
    // manufacture a second label/binding when one is already present.
    if (hasBoundText(element)) delete clean.label;
    if (isNativeElement(element)) {
      if (clean.startBinding) delete clean.start;
      if (clean.endBinding) delete clean.end;
    }
    if (clean.label?.text && !clean.label.id) {
      clean.label = { ...clean.label, id: stableLabelId(clean.id) };
    }
    if (clean.label?.text && !clean.label.fontFamily) {
      clean.label = { ...clean.label, fontFamily: DEFAULT_SKETCH_FONT };
    }
    // Stored coordinates (including server-generated bound labels) are top
    // left. The creation API interprets centered text coordinates as anchors.
    // Measure at top-left, then restore the requested alignment below.
    if (clean.type === "text") {
      clean.textAlign = "left";
      clean.verticalAlign = "top";
    }
    return clean;
  });

  const converted = needsMaterialization
    ? convertToExcalidrawElements(
      // Saved frames have frameId membership, not the constructor's children
      // DSL. They need no conversion and must keep their human-sized bounds.
      skeletons.filter((element) => element.type !== "frame" && element.type !== "magicframe") as Parameters<typeof convertToExcalidrawElements>[0],
      { regenerateIds: false },
    ) as ExcalidrawElement[]
    : source as unknown as ExcalidrawElement[];
  const convertedById = new Map(converted.map((element) => [element.id, element]));
  const materialized = source.map((original) => {
    const convertedElement = convertedById.get(String(original.id));
    if (!convertedElement) return original as unknown as ExcalidrawElement;
    if (isNativeElement(original)) {
      // Existing elements served as anchors for new connectors during the
      // scratch conversion. Keep every native property, accepting only new
      // binding references (e.g. an arrow added to a human-edited shape).
      const bindings = new Map(
        [...((original.boundElements || []) as any[]), ...(convertedElement.boundElements || [])]
          .map((bound) => [bound.id, bound]),
      );
      return { ...original, boundElements: [...bindings.values()] } as unknown as ExcalidrawElement;
    }
    if (convertedElement.type === "text") {
      return {
        ...convertedElement,
        ...(original.autoResize === false && typeof original.width === "number"
          ? { width: original.width } : {}),
        textAlign: original.textAlign || "left",
        verticalAlign: original.verticalAlign || "top",
      } as ExcalidrawElement;
    }
    return convertedElement;
  }).concat(converted.filter((element) => !sourceIds.has(element.id)));
  const materializedIds = new Set(materialized.map((element) => String(element.id)));
  const canonical = materialized.map((element) => {
    const clean: Record<string, any> = { ...element };
    // Persist only canonical Excalidraw bindings. These semantic hints did
    // their job during conversion and otherwise cause duplicate labels on the
    // next mixed human/agent revision.
    delete clean.label;
    delete clean.start;
    delete clean.end;
    if (Array.isArray(clean.boundElements)) {
      clean.boundElements = clean.boundElements.filter((bound: any) =>
        bound && materializedIds.has(String(bound.id || "")));
    }
    return clean as ExcalidrawElement;
  });
  return layoutBoundLabels(restoreElements(canonical, null, { repairBindings: true }));
}
