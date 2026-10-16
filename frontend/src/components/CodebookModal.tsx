"use client";

// Modal that renders one page of a codebook PDF with a clause boxed — the
// clause-side half of cross-referencing. Reuses the pdfjs load/render/cancel
// pattern from PlanViewer; the highlight is a positioned div in the same scaled
// coordinates. A clause with no bbox (non-docling parse) just shows the page.

import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";

import { api } from "@/lib/api";

export interface CodebookView {
  documentId: string;
  page: number; // 0-indexed
  bbox: number[] | null; // [x0, y0, x1, y1] in PDF points, origin top-left
}

interface Props {
  view: CodebookView | null;
  onClose: () => void;
}

const MAX_CANVAS_PX = 8192;

export default function CodebookModal({ view, onClose }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pdfCacheRef = useRef<Map<string, PDFDocumentProxy>>(new Map());
  const renderTaskRef = useRef<RenderTask | null>(null);
  const [scale, setScale] = useState(1);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!view) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view, onClose]);

  useEffect(() => {
    const cache = pdfCacheRef.current;
    return () => {
      renderTaskRef.current?.cancel();
      for (const pdf of cache.values()) void pdf.destroy();
      cache.clear();
    };
  }, []);

  useEffect(() => {
    if (!view) return;
    let cancelled = false;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
        let pdf = pdfCacheRef.current.get(view.documentId);
        if (!pdf) {
          pdf = await pdfjs.getDocument(api.pdfUrl(view.documentId)).promise;
          pdfCacheRef.current.set(view.documentId, pdf);
        }
        if (cancelled) return;
        const page = await pdf.getPage(view.page + 1);
        if (cancelled) return;
        const unscaled = page.getViewport({ scale: 1 });
        const s = Math.min(720, unscaled.width * 1.5) / unscaled.width;
        let dpr = window.devicePixelRatio || 1;
        dpr = Math.min(dpr, MAX_CANVAS_PX / (Math.max(unscaled.width, unscaled.height) * s));
        const viewport = page.getViewport({ scale: s * dpr });
        const canvas = canvasRef.current;
        if (!canvas) return;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.style.width = `${viewport.width / dpr}px`;
        canvas.style.height = `${viewport.height / dpr}px`;
        renderTaskRef.current?.cancel();
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        const task = page.render({ canvasContext: ctx, viewport });
        renderTaskRef.current = task;
        await task.promise;
        if (!cancelled) {
          setScale(s);
          setError(null);
        }
      } catch (e) {
        if ((e as { name?: string }).name === "RenderingCancelledException") return;
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
    };
  }, [view]);

  if (!view) return null;

  const highlight = view.bbox
    ? {
        left: view.bbox[0] * scale,
        top: view.bbox[1] * scale,
        width: (view.bbox[2] - view.bbox[0]) * scale,
        height: (view.bbox[3] - view.bbox[1]) * scale,
      }
    : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-surface-0/70 p-6"
      onClick={onClose}
    >
      <div
        className="flex max-h-[88vh] max-w-[760px] flex-col overflow-hidden rounded-xl border border-edge bg-surface-1"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-edge px-4 py-2">
          <span className="text-xs text-ink-dim">Codebook · page {view.page + 1}</span>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded px-2 text-ink-dim hover:bg-surface-2 hover:text-ink"
          >
            ×
          </button>
        </div>
        <div className="overflow-auto p-3">
          {error ? (
            <p className="text-fail">Couldn’t load the codebook page: {error}</p>
          ) : (
            <div className="relative inline-block">
              <canvas ref={canvasRef} className="block rounded bg-white" />
              {highlight && (
                <div
                  className="pointer-events-none absolute rounded-sm border-2 border-accent bg-accent/15"
                  style={highlight}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
