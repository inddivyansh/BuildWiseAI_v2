"use client";

// Right-hand pane: the codebook clause tree, annotated with verdicts. Clauses
// group by document when there's more than one codebook; clicking a verdict-
// annotated clause selects its asset (reverse sync with the plan canvas).

import { useEffect, useMemo, useRef } from "react";

import {
  formatInches,
  prefersReducedMotion,
  verdictBadgeClass,
  verdictCardClass,
  verdictGlyph,
  verdictLabel,
  VERDICT_SEVERITY,
} from "@/lib/verdicts";
import { assetDisplayName, assetPrimaryMeasure } from "@/lib/assets";
import type { CodebookView } from "@/components/CodebookModal";
import type { Asset, Clause, Doc, Sheet, VerdictEdge } from "@/lib/types";

const MAX_ASSET_CHIPS = 6; // cap the "applies to" strip; rest collapse to "+N more"

interface Props {
  clauses: Clause[];
  documents: Doc[];
  sheet: Sheet | null;
  selectedAsset: Asset | null;
  onSelectAsset: (asset: Asset | null) => void;
  focusClauseId?: string | null; // scroll target set by the inspector
  onViewClause?: (view: CodebookView) => void;
}

/**
 * The semantic pane: the codebook's clause list (grouped per codebook when
 * several are loaded). Selecting an asset on the plan highlights its
 * governing clauses and scrolls to the worst one; clauses governing any
 * asset on the current sheet are clickable and select that asset back on
 * the canvas.
 */
export default function CodePane({
  clauses,
  documents,
  sheet,
  selectedAsset,
  onSelectAsset,
  focusClauseId,
  onViewClause,
}: Props) {
  const refs = useRef<Record<string, HTMLDivElement | null>>({});

  const selectedVerdicts = useMemo(
    () => new Map((selectedAsset?.verdicts ?? []).map((v) => [v.regulation_id, v])),
    [selectedAsset],
  );

  // regulation_id -> every asset on the current sheet it governs, worst-first
  // (one clause can govern many assets — e.g. a door-width rule over every door).
  const assetsByRegulation = useMemo(() => {
    const byReg = new Map<string, Map<string, { asset: Asset; verdict: VerdictEdge }>>();
    for (const asset of sheet?.assets ?? []) {
      for (const verdict of asset.verdicts) {
        let perAsset = byReg.get(verdict.regulation_id);
        if (!perAsset) {
          perAsset = new Map();
          byReg.set(verdict.regulation_id, perAsset);
        }
        const existing = perAsset.get(asset.id);
        // one edge per asset — keep its worst verdict if runs left several.
        if (!existing || VERDICT_SEVERITY[verdict.verdict] < VERDICT_SEVERITY[existing.verdict.verdict]) {
          perAsset.set(asset.id, { asset, verdict });
        }
      }
    }
    const out = new Map<string, { asset: Asset; verdict: VerdictEdge }[]>();
    for (const [reg, perAsset] of byReg) {
      out.set(
        reg,
        [...perAsset.values()].sort(
          (a, b) => VERDICT_SEVERITY[a.verdict.verdict] - VERDICT_SEVERITY[b.verdict.verdict],
        ),
      );
    }
    return out;
  }, [sheet]);

  const codebooks = useMemo(
    () => documents.filter((d) => d.kind === "codebook"),
    [documents],
  );

  const groups = useMemo(() => {
    if (codebooks.length <= 1) return [{ codebook: null as Doc | null, clauses }];
    return codebooks
      .map((codebook) => ({
        codebook: codebook as Doc | null,
        clauses: clauses.filter((c) => c.document_id === codebook.id),
      }))
      .filter((group) => group.clauses.length > 0);
  }, [codebooks, clauses]);

  useEffect(() => {
    if (!selectedAsset) return;
    const ranked = [...selectedAsset.verdicts].sort(
      (a, b) => VERDICT_SEVERITY[a.verdict] - VERDICT_SEVERITY[b.verdict],
    );
    const target = ranked[0];
    if (target) {
      refs.current[target.regulation_id]?.scrollIntoView({
        behavior: prefersReducedMotion() ? "auto" : "smooth",
        block: "center",
      });
    }
  }, [selectedAsset]);

  // Scroll to a clause the inspector focused (asset → clause cross-reference).
  useEffect(() => {
    if (!focusClauseId) return;
    refs.current[focusClauseId]?.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
      block: "center",
    });
  }, [focusClauseId]);

  if (clauses.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="max-w-xs text-center text-ink-dim">
          No codebook ingested yet — upload one and run verification to see
          governing clauses here.
        </p>
      </div>
    );
  }

  return (
    <div>
      {groups.map((group, groupIndex) => (
        <section key={group.codebook?.id ?? "all"}>
          {group.codebook && (
            <h3 className="sticky top-0 z-10 border-b border-edge bg-surface-1/95 px-4 py-2 font-medium backdrop-blur">
              {group.codebook.filename}
            </h3>
          )}
          {group.clauses.map((clause) => {
            const verdict = selectedVerdicts.get(clause.id);
            const governed = assetsByRegulation.get(clause.id);
            const worst = governed?.[0]?.asset; // worst-first: the row selects this one
            const clickable = worst !== undefined;
            return (
              <div
                key={`${groupIndex}-${clause.id}`}
                ref={(el) => {
                  refs.current[clause.id] = el;
                }}
                role={clickable ? "button" : undefined}
                tabIndex={clickable ? 0 : undefined}
                onClick={clickable ? () => onSelectAsset(worst) : undefined}
                onKeyDown={
                  clickable
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onSelectAsset(worst);
                        }
                      }
                    : undefined
                }
                className={`border-b border-edge px-4 py-3 scroll-mt-10 transition-opacity [content-visibility:auto] [contain-intrinsic-size:auto_120px] ${
                  verdict
                    ? `border-l-2 ${verdictCardClass(verdict.verdict)}`
                    : "border-l-2 border-l-transparent"
                } ${clickable ? "cursor-pointer hover:bg-surface-2 focus:outline-2 focus:-outline-offset-2 focus:outline-accent" : ""} ${
                  selectedAsset && !verdict ? "opacity-40" : ""
                }`}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <h4 className="font-medium">
                    <span className="font-mono">{clause.clause_id}</span> {clause.title}
                  </h4>
                  <div className="flex shrink-0 items-center gap-1">
                    {onViewClause && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onViewClause({
                            documentId: clause.document_id,
                            page: clause.page,
                            bbox: clause.bbox,
                          });
                        }}
                        title="View in codebook"
                        aria-label="View in codebook"
                        className="rounded px-1 text-ink-dim hover:bg-surface-2 hover:text-ink"
                      >
                        ⧉
                      </button>
                    )}
                  </div>
                </div>
                {clause.hierarchy_path && (
                  <div className="mt-0.5 text-xs text-ink-dim">{clause.hierarchy_path}</div>
                )}
                <div className="mt-1.5 text-ink-dim whitespace-pre-wrap">{clause.text}</div>
                {verdict && (
                  <div className="mt-2.5">
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${verdictBadgeClass(verdict.verdict)}`}
                    >
                      {verdictGlyph(verdict.verdict)} {verdictLabel(verdict.verdict)}
                    </span>
                    {verdict.measured != null && (
                      <span className="ml-2 text-xs text-ink-dim">
                        measured <span className="font-mono">{formatInches(verdict.measured)}</span>{" "}
                        · required <span className="font-mono">{verdict.required}</span>
                      </span>
                    )}
                    {verdict.reason && (
                      <div className="mt-1 text-xs text-ink-dim">{verdict.reason}</div>
                    )}
                  </div>
                )}

                {/* Every asset this clause governs — the mirror of the inspector's
                    "governing clauses" list. Each chip selects that asset. */}
                {governed && governed.length > 0 && (
                  <div className="mt-2 flex flex-wrap items-center gap-1">
                    <span className="text-[10px] uppercase tracking-wide text-ink-dim">
                      Applies to
                    </span>
                    {governed.slice(0, MAX_ASSET_CHIPS).map(({ asset, verdict: v }) => {
                      // measurement disambiguates otherwise-identical labels
                      // (five unnamed doors read as "Door · 30″", "Door · 36″", …)
                      const measure = assetPrimaryMeasure(asset);
                      return (
                        <button
                          key={asset.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            onSelectAsset(asset);
                          }}
                          title={`Select ${assetDisplayName(asset)}`}
                          className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-xs font-medium ${verdictBadgeClass(
                            v.verdict,
                          )} ${asset.id === selectedAsset?.id ? "ring-1 ring-accent" : ""}`}
                        >
                          {verdictGlyph(v.verdict)} {assetDisplayName(asset)}
                          {measure && <span className="font-mono opacity-80">· {measure}</span>}
                        </button>
                      );
                    })}
                    {governed.length > MAX_ASSET_CHIPS && (
                      <span className="text-xs text-ink-dim">
                        +{governed.length - MAX_ASSET_CHIPS} more
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}
