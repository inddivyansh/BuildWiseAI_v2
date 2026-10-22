// Single source of truth for how an asset presents in the UI — the label,
// the compact type code drawn on the canvas, the one measurement worth showing
// inline, and how trustworthy its geometry is. Mirrors lib/verdicts.ts so the
// canvas overlay, legend, inspector, and asset index never drift apart.

import type { Asset } from "./types";
import { formatInches, worstBoxVerdict } from "./verdicts";

const TYPE_LABEL: Record<string, string> = {
  door: "Door",
  fire_exit: "Fire exit",
  window: "Window",
  stair: "Stair",
  ramp: "Ramp",
  room: "Room",
  corridor: "Corridor",
  other: "Element",
};

// Compact code drawn as the always-on marker on every box; the legend spells it
// out. Kept ASCII so it renders identically on the canvas and in the DOM.
const TYPE_CODE: Record<string, string> = {
  door: "D",
  fire_exit: "FE",
  window: "W",
  stair: "ST",
  ramp: "RP",
  room: "RM",
  corridor: "CR",
  other: "•",
};

/** Human label for an asset type ("door" → "Door"). */
export function assetTypeLabel(type: string): string {
  return TYPE_LABEL[type] ?? "Element";
}

/** Compact type marker for dense canvas/legend use ("door" → "D"). */
export function assetTypeCode(type: string): string {
  return TYPE_CODE[type] ?? "•";
}

/** What to show as the asset's name — its label, or the type word when the
 * drawing didn't tag it. The fix for blank boxes: a name is always present. */
export function assetDisplayName(asset: Asset): string {
  return asset.label.trim() || assetTypeLabel(asset.type);
}

// Measurements, most-salient first; the first one present is shown inline.
const MEASURE_ORDER = [
  "clear_width",
  "area_m2",
  "riser_height",
  "tread_depth",
  "opening_height",
  "landing_length",
  "maneuvering_clearance",
  "threshold_height",
  "slope",
] as const;

function formatMeasure(key: string, value: number): string {
  if (key === "area_m2") return `${value} m²`;
  if (key === "slope") return `${value}`;
  return formatInches(value);
}

/** The single measurement worth showing beside the name, or null when none. */
export function assetPrimaryMeasure(asset: Asset): string | null {
  for (const key of MEASURE_ORDER) {
    const value = asset.measurements[key];
    if (value != null) return formatMeasure(key, value);
  }
  return null;
}

/** Every measurement, formatted, for the inspector's detail list. */
export function assetMeasurements(asset: Asset): { label: string; value: string }[] {
  return Object.entries(asset.measurements).map(([key, value]) => ({
    label: key.replace(/_/g, " "),
    value: formatMeasure(key, value),
  }));
}

/** How much to trust an asset's geometry, by provenance. `vlm-only` boxes were
 * not confirmed against vector or pixel geometry, so they warrant a look. */
export function sourceQuality(source: Asset["source"]): { label: string; confirmed: boolean } {
  if (source === "vlm-only") return { label: "Needs review", confirmed: false };
  if (source === "detail-referenced") return { label: "From referenced sheet", confirmed: true };
  return { label: "Confirmed", confirmed: true };
}

// ---- visibility filtering (legend ⇄ canvas ⇄ index share one key scheme) ----

/** Filter key for an asset's worst verdict ('verdict:VIOLATES', or
 * 'verdict:UNCHECKED' when it has no verdicts yet). */
export function verdictKeyOf(asset: Asset): string {
  return `verdict:${worstBoxVerdict(asset.verdicts) ?? "UNCHECKED"}`;
}

/** Filter key for an asset type ('type:door'). */
export function typeKeyOf(type: string): string {
  return `type:${type}`;
}

/** An asset is hidden when either its verdict key or its type key is toggled
 * off in the shared `hidden` set. */
export function isAssetHidden(asset: Asset, hidden: Set<string>): boolean {
  return hidden.has(verdictKeyOf(asset)) || hidden.has(typeKeyOf(asset.type));
}
