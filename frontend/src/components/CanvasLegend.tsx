"use client";

// Canvas overlay that is both the key and the filter: it lists the verdicts and
// asset types present on the sheet (with live counts) and lets you toggle each
// on/off. Toggling writes to the shared `hidden` key set the canvas and the
// asset index both read, so one control declutters everything at once.

import { useState } from "react";

import { assetTypeLabel, assetTypeCode, typeKeyOf, verdictKeyOf } from "@/lib/assets";
import { verdictGlyph, verdictLabel } from "@/lib/verdicts";
import type { Asset, Verdict } from "@/lib/types";

interface Props {
  assets: Asset[];
  hidden: Set<string>;
  onToggle: (key: string) => void;
}

const VERDICT_ORDER: (Verdict | "UNCHECKED")[] = [
  "VIOLATES",
  "NEEDS_REVIEW",
  "COMPLIES_WITH",
  "UNCHECKED",
];

const VERDICT_TONE: Record<Verdict | "UNCHECKED", string> = {
  VIOLATES: "text-fail",
  NEEDS_REVIEW: "text-review",
  COMPLIES_WITH: "text-pass",
  UNCHECKED: "text-ink-dim",
};

function verdictKey(v: Verdict | "UNCHECKED"): string {
  return `verdict:${v}`;
}

function label(v: Verdict | "UNCHECKED"): string {
  return v === "UNCHECKED" ? "Unchecked" : verdictLabel(v);
}

function glyph(v: Verdict | "UNCHECKED"): string {
  return v === "UNCHECKED" ? "○" : verdictGlyph(v);
}

export default function CanvasLegend({ assets, hidden, onToggle }: Props) {
  const [open, setOpen] = useState(true);

  const verdictCounts = new Map<string, number>();
  const typeCounts = new Map<string, number>();
  for (const asset of assets) {
    verdictCounts.set(verdictKeyOf(asset), (verdictCounts.get(verdictKeyOf(asset)) ?? 0) + 1);
    typeCounts.set(asset.type, (typeCounts.get(asset.type) ?? 0) + 1);
  }

  const rowClass = (key: string) =>
    `flex w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-surface-2 ${
      hidden.has(key) ? "opacity-40" : ""
    }`;

  return (
    <div className="absolute top-5 left-5 z-10 w-44 rounded-lg border border-edge bg-surface-1/90 text-xs backdrop-blur">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between rounded-t-lg px-2.5 py-1.5 font-medium text-ink-dim hover:text-ink"
      >
        Legend
        <span aria-hidden>{open ? "–" : "+"}</span>
      </button>
      {open && (
        <div className="border-t border-edge px-1.5 py-1.5">
          {VERDICT_ORDER.filter((v) => verdictCounts.get(verdictKey(v))).map((v) => {
            const key = verdictKey(v);
            return (
              <button
                key={key}
                onClick={() => onToggle(key)}
                aria-pressed={!hidden.has(key)}
                className={rowClass(key)}
              >
                <span className={`w-3 text-center ${VERDICT_TONE[v]}`}>{glyph(v)}</span>
                <span className="flex-1 text-ink">{label(v)}</span>
                <span className="text-ink-dim tabular-nums">{verdictCounts.get(key)}</span>
              </button>
            );
          })}
          {typeCounts.size > 0 && <div className="my-1 border-t border-edge" />}
          {[...typeCounts.keys()].sort().map((type) => {
            const key = typeKeyOf(type);
            return (
              <button
                key={key}
                onClick={() => onToggle(key)}
                aria-pressed={!hidden.has(key)}
                className={rowClass(key)}
              >
                <span className="w-6 rounded bg-surface-2 text-center font-mono text-[10px] text-ink-dim">
                  {assetTypeCode(type)}
                </span>
                <span className="flex-1 text-ink">{assetTypeLabel(type)}</span>
                <span className="text-ink-dim tabular-nums">{typeCounts.get(type)}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
