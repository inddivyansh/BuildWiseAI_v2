"use client";

// Browse every asset on the current sheet, grouped worst-verdict-first, with a
// search box. Honors the legend's visibility filter and selecting a row syncs
// the canvas + inspector — the browse half of the review surface.

import { useState } from "react";

import {
  assetDisplayName,
  assetPrimaryMeasure,
  assetTypeCode,
  assetTypeLabel,
  isAssetHidden,
} from "@/lib/assets";
import { verdictGlyph, worstBoxVerdict } from "@/lib/verdicts";
import type { Asset, Verdict } from "@/lib/types";

interface Props {
  assets: Asset[];
  hidden: Set<string>;
  selectedAssetId: string | null;
  onSelect: (asset: Asset) => void;
}

const GROUPS: { key: Verdict | "UNCHECKED"; label: string; tone: string }[] = [
  { key: "VIOLATES", label: "Violations", tone: "text-fail" },
  { key: "NEEDS_REVIEW", label: "Needs review", tone: "text-review" },
  { key: "COMPLIES_WITH", label: "Complies", tone: "text-pass" },
  { key: "UNCHECKED", label: "Unchecked", tone: "text-ink-dim" },
];

export default function AssetIndex({ assets, hidden, selectedAssetId, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();

  const visible = assets.filter(
    (a) =>
      !isAssetHidden(a, hidden) &&
      (q === "" ||
        assetDisplayName(a).toLowerCase().includes(q) ||
        assetTypeLabel(a.type).toLowerCase().includes(q)),
  );

  const grouped = new Map<string, Asset[]>();
  for (const a of visible) {
    const key = worstBoxVerdict(a.verdicts) ?? "UNCHECKED";
    const arr = grouped.get(key);
    if (arr) arr.push(a);
    else grouped.set(key, [a]);
  }

  return (
    <div>
      <div className="sticky top-0 z-10 border-b border-edge bg-surface-1/95 px-3 py-2 backdrop-blur">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search assets"
          aria-label="Search assets"
          className="w-full rounded-lg border border-edge bg-surface-2 px-2.5 py-1 text-xs placeholder:text-ink-dim/60 focus:outline-2 focus:outline-accent"
        />
      </div>

      {visible.length === 0 ? (
        <p className="px-4 py-6 text-xs text-ink-dim">
          No assets match — clear the search or the legend filters.
        </p>
      ) : (
        GROUPS.filter((g) => grouped.get(g.key)?.length).map((g) => (
          <section key={g.key}>
            <h4 className={`px-4 pt-3 pb-1 text-xs font-medium ${g.tone}`}>
              {g.label} ({grouped.get(g.key)!.length})
            </h4>
            {grouped.get(g.key)!.map((a) => {
              const worst = worstBoxVerdict(a.verdicts);
              const measure = assetPrimaryMeasure(a);
              return (
                <button
                  key={a.id}
                  onClick={() => onSelect(a)}
                  className={`flex w-full items-center gap-2 border-b border-edge px-4 py-2 text-left text-xs hover:bg-surface-2 focus:outline-2 focus:-outline-offset-2 focus:outline-accent ${
                    selectedAssetId === a.id ? "bg-surface-2" : ""
                  }`}
                >
                  <span className="w-6 shrink-0 rounded bg-surface-2 text-center font-mono text-[10px] text-ink-dim">
                    {assetTypeCode(a.type)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-ink">{assetDisplayName(a)}</span>
                  {measure && <span className="shrink-0 font-mono text-ink-dim">{measure}</span>}
                  {worst && (
                    <span aria-hidden className="shrink-0">
                      {verdictGlyph(worst)}
                    </span>
                  )}
                </button>
              );
            })}
          </section>
        ))
      )}
    </div>
  );
}
