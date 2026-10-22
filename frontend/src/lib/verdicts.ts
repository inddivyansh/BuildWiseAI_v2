// Single source of truth for how verdicts look. Colors come from the CSS
// theme tokens in globals.css so canvas drawing and Tailwind classes can
// never drift apart. Verdicts are always rendered with a glyph + word, not
// color alone.

import type { Verdict } from "./types";

const TOKEN: Record<Verdict, string> = {
  VIOLATES: "--color-fail",
  COMPLIES_WITH: "--color-pass",
  NEEDS_REVIEW: "--color-review",
};

const FALLBACK: Record<Verdict, string> = {
  VIOLATES: "#f87171",
  COMPLIES_WITH: "#34d399",
  NEEDS_REVIEW: "#fbbf24",
};

const cache = new Map<Verdict, string>();

/** Resolved CSS color for a verdict (client-only; falls back during SSR). */
export function verdictColor(verdict: Verdict): string {
  const cached = cache.get(verdict);
  if (cached) return cached;
  if (typeof window === "undefined") return FALLBACK[verdict];
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(TOKEN[verdict])
    .trim();
  const color = value || FALLBACK[verdict];
  cache.set(verdict, color);
  return color;
}

export function verdictGlyph(verdict: Verdict): string {
  return verdict === "VIOLATES" ? "✕" : verdict === "COMPLIES_WITH" ? "✓" : "?";
}

export function verdictLabel(verdict: Verdict): string {
  return verdict === "VIOLATES"
    ? "Violates"
    : verdict === "COMPLIES_WITH"
      ? "Complies"
      : "Needs review";
}

/** Worst-first ordering used for badge/scroll priority. */
export const VERDICT_SEVERITY: Record<Verdict, number> = {
  VIOLATES: 0,
  NEEDS_REVIEW: 1,
  COMPLIES_WITH: 2,
};

/** Highest-priority verdict for an asset: red beats green beats amber on the
 * canvas (a measured pass is more informative than an unmeasured review). */
export function worstBoxVerdict(verdicts: { verdict: Verdict }[]): Verdict | null {
  if (verdicts.some((v) => v.verdict === "VIOLATES")) return "VIOLATES";
  if (verdicts.some((v) => v.verdict === "COMPLIES_WITH")) return "COMPLIES_WITH";
  if (verdicts.length > 0) return "NEEDS_REVIEW";
  return null;
}

/** Centralized unit formatting — the one place to change when measurements
 * grow units beyond inches. */
export function formatInches(value: number): string {
  return `${value}″`;
}

/** Pill styling for a verdict badge — shared so every pane's badge matches. */
export function verdictBadgeClass(verdict: Verdict): string {
  return verdict === "VIOLATES"
    ? "bg-fail/15 text-fail"
    : verdict === "COMPLIES_WITH"
      ? "bg-pass/15 text-pass"
      : "bg-review/15 text-review";
}

/** Left-border + tint for a verdict-annotated card. */
export function verdictCardClass(verdict: Verdict): string {
  return verdict === "VIOLATES"
    ? "border-l-fail bg-fail/10"
    : verdict === "COMPLIES_WITH"
      ? "border-l-pass bg-pass/10"
      : "border-l-review bg-review/10";
}

/** Whether the browser reports a reduced-motion preference (client-only). */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
