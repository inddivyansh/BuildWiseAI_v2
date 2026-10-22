import { describe, expect, it } from "vitest";

import type { Asset, Verdict, VerdictEdge } from "./types";
import {
  assetDisplayName,
  assetPrimaryMeasure,
  assetTypeCode,
  assetTypeLabel,
  isAssetHidden,
  sourceQuality,
  typeKeyOf,
  verdictKeyOf,
} from "./assets";

function verdict(v: Verdict): VerdictEdge {
  return {
    verdict: v,
    run_id: "r",
    measured: null,
    required: null,
    reason: "",
    regulation_id: "reg",
    clause_id: "1",
    clause_page: 0,
    clause_bbox: null,
    clause_document_id: "d",
  };
}

function asset(partial: Partial<Asset> = {}): Asset {
  return {
    id: "a1",
    type: "door",
    label: "",
    bbox: [0, 0, 10, 10],
    confidence: 0.9,
    source: "vector-snapped",
    measurements: {},
    verdicts: [],
    ...partial,
  };
}

describe("assetDisplayName", () => {
  it("uses the label when present", () => {
    expect(assetDisplayName(asset({ label: "D3" }))).toBe("D3");
  });
  it("falls back to the type word when unnamed", () => {
    expect(assetDisplayName(asset({ type: "window" }))).toBe("Window");
    expect(assetDisplayName(asset({ type: "fire_exit", label: "  " }))).toBe("Fire exit");
  });
});

describe("assetPrimaryMeasure", () => {
  it("formats clear_width and area", () => {
    expect(assetPrimaryMeasure(asset({ measurements: { clear_width: 36 } }))).toBe("36″");
    expect(assetPrimaryMeasure(asset({ measurements: { area_m2: 18 } }))).toBe("18 m²");
  });
  it("follows the measure order (clear_width before area)", () => {
    expect(
      assetPrimaryMeasure(asset({ measurements: { area_m2: 18, clear_width: 36 } })),
    ).toBe("36″");
  });
  it("is null when there is nothing measurable", () => {
    expect(assetPrimaryMeasure(asset())).toBeNull();
  });
});

describe("type + source helpers", () => {
  it("codes and labels types, with an Element fallback", () => {
    expect(assetTypeCode("door")).toBe("D");
    expect(assetTypeCode("stair")).toBe("ST");
    expect(assetTypeLabel("corridor")).toBe("Corridor");
    expect(assetTypeLabel("weird")).toBe("Element");
  });
  it("flags vlm-only as needs review", () => {
    expect(sourceQuality("vlm-only").confirmed).toBe(false);
    expect(sourceQuality("raster-snapped").confirmed).toBe(true);
  });
});

describe("visibility filtering", () => {
  it("keys and hides by verdict", () => {
    const a = asset({ verdicts: [verdict("VIOLATES")] });
    expect(verdictKeyOf(a)).toBe("verdict:VIOLATES");
    expect(isAssetHidden(a, new Set(["verdict:VIOLATES"]))).toBe(true);
    expect(isAssetHidden(a, new Set())).toBe(false);
  });
  it("keys and hides by type", () => {
    expect(typeKeyOf("window")).toBe("type:window");
    expect(isAssetHidden(asset({ type: "window" }), new Set(["type:window"]))).toBe(true);
  });
  it("treats verdict-less assets as unchecked", () => {
    expect(verdictKeyOf(asset())).toBe("verdict:UNCHECKED");
  });
});
