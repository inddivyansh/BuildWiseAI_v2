import { describe, expect, it } from "vitest";

import type { Verdict } from "./types";
import {
  formatInches,
  VERDICT_SEVERITY,
  verdictBadgeClass,
  verdictCardClass,
  worstBoxVerdict,
} from "./verdicts";

const v = (verdict: Verdict) => ({ verdict });

describe("worstBoxVerdict", () => {
  it("ranks violates over complies over review", () => {
    expect(worstBoxVerdict([v("COMPLIES_WITH"), v("VIOLATES")])).toBe("VIOLATES");
    expect(worstBoxVerdict([v("NEEDS_REVIEW"), v("COMPLIES_WITH")])).toBe("COMPLIES_WITH");
    expect(worstBoxVerdict([v("NEEDS_REVIEW")])).toBe("NEEDS_REVIEW");
  });
  it("is null with no verdicts", () => {
    expect(worstBoxVerdict([])).toBeNull();
  });
});

describe("formatInches", () => {
  it("appends the inch mark", () => {
    expect(formatInches(36)).toBe("36″");
  });
});

describe("verdict styling", () => {
  it("maps each verdict to its tone", () => {
    expect(verdictBadgeClass("VIOLATES")).toContain("fail");
    expect(verdictBadgeClass("NEEDS_REVIEW")).toContain("review");
    expect(verdictCardClass("COMPLIES_WITH")).toContain("pass");
  });
});

describe("VERDICT_SEVERITY", () => {
  it("orders violates < review < complies", () => {
    expect(VERDICT_SEVERITY.VIOLATES).toBeLessThan(VERDICT_SEVERITY.NEEDS_REVIEW);
    expect(VERDICT_SEVERITY.NEEDS_REVIEW).toBeLessThan(VERDICT_SEVERITY.COMPLIES_WITH);
  });
});
