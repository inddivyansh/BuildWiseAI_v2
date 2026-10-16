"use client";

// Left-hand pane: renders a sheet's PDF page and overlays asset boxes coloured
// by verdict. Render tasks are cancelled and PDF proxies cached per document,
// so sheet switches and resizes never leak work or corrupt the canvas.

import { useCallback, useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";

import { api } from "@/lib/api";
import CanvasLegend from "@/components/CanvasLegend";
import {
  prefersReducedMotion,
  verdictColor,
  verdictGlyph,
  worstBoxVerdict,
} from "@/lib/verdicts";
import {
  assetDisplayName,
  assetPrimaryMeasure,
  assetTypeCode,
  isAssetHidden,
} from "@/lib/assets";
import type { Asset, Sheet } from "@/lib/types";

const BOX_PAD = 6; // PDF points of breathing room around asset bboxes
const MAX_FIT_SCALE = 2.5; // cap for the zoom=1 fit-width scale
const MAX_CANVAS_PX = 8192; // backing-store safety cap per dimension
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 6;
const ZOOM_STEP = 1.25;
const DRAG_THRESHOLD_PX = 4; // pointer travel that turns a click into a pan
const LABEL_MIN_WIDTH_PX = 110; // boxes narrower than this hide their chip
const UNVERDICTED = "#98a1b3"; // --color-ink-dim; assets with no checks yet

interface Props {
  sheet: Sheet;
  selectedAssetId: string | null;
  onSelectAsset: (asset: Asset | null) => void;
  hidden: Set<string>; // verdict:/type: keys toggled off in the legend
  onToggleHidden: (key: string) => void;
}

function isRenderingCancelled(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: string }).name === "RenderingCancelledException"
  );
}

function boxArea(asset: Asset): number {
  const [x0, y0, x1, y1] = asset.bbox;
  return Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
}

/** All assets under a point, smallest first — so nested/overlapping small
 * assets (a door inside a room box) are reachable. */
function hitsAt(sheet: Sheet, x: number, y: number, hidden: Set<string>): Asset[] {
  return sheet.assets
    .filter(
      (asset) =>
        !isAssetHidden(asset, hidden) &&
        x >= asset.bbox[0] - BOX_PAD &&
        x <= asset.bbox[2] + BOX_PAD &&
        y >= asset.bbox[1] - BOX_PAD &&
        y <= asset.bbox[3] + BOX_PAD,
    )
    .sort((a, b) => boxArea(a) - boxArea(b));
}

export default function PlanViewer({
  sheet,
  selectedAssetId,
  onSelectAsset,
  hidden,
  onToggleHidden,
}: Props) {
  const pdfCanvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const pdfCacheRef = useRef<Map<string, PDFDocumentProxy>>(new Map());
  const renderTaskRef = useRef<RenderTask | null>(null);
  const dragRef = useRef({ active: false, moved: false, x: 0, y: 0, left: 0, top: 0 });
  const pendingScrollRef = useRef<{ left: number; top: number } | null>(null);

  const [wrapperWidth, setWrapperWidth] = useState(0);
  const [zoom, setZoom] = useState(1); // 1 = fit pane width
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [render, setRender] = useState<{ scale: number; dpr: number } | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);

  // Track pane width so the canvas reflows with the layout.
  useEffect(() => {
    const element = wrapperRef.current;
    if (!element) return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setWrapperWidth(element.clientWidth));
    });
    observer.observe(element);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  // Destroy cached PDF proxies (and any in-flight render) on unmount.
  useEffect(() => {
    const cache = pdfCacheRef.current;
    return () => {
      renderTaskRef.current?.cancel();
      for (const pdf of cache.values()) void pdf.destroy();
      cache.clear();
    };
  }, []);

  // Reset zoom when the sheet changes: each sheet starts fitted.
  useEffect(() => {
    setZoom(1);
    setHoverId(null);
  }, [sheet.id]);

  // Render the PDF page. Re-runs on sheet switch, pane resize, and zoom;
  // in-flight renders are cancelled so rapid changes can't corrupt the
  // canvas. The document proxy is cached per document.
  useEffect(() => {
    if (wrapperWidth === 0) return; // wait for the first measurement
    let cancelled = false;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
        let pdf = pdfCacheRef.current.get(sheet.document_id);
        if (!pdf) {
          pdf = await pdfjs.getDocument(api.pdfUrl(sheet.document_id)).promise;
          pdfCacheRef.current.set(sheet.document_id, pdf);
        }
        if (cancelled) return;
        const page = await pdf.getPage(sheet.page_number + 1);
        if (cancelled) return;

        const fit = Math.min((wrapperWidth - 24) / sheet.width, MAX_FIT_SCALE);
        const scale = fit * zoom;
        let dpr = window.devicePixelRatio || 1;
        // Keep the backing store within safe canvas limits at high zoom.
        const largestSide = Math.max(sheet.width, sheet.height) * scale;
        dpr = Math.min(dpr, MAX_CANVAS_PX / largestSide);
        const viewport = page.getViewport({ scale: scale * dpr });
        const canvas = pdfCanvasRef.current;
        const overlay = overlayRef.current;
        if (!canvas || !overlay) return;
        for (const target of [canvas, overlay]) {
          target.width = viewport.width;
          target.height = viewport.height;
          target.style.width = `${viewport.width / dpr}px`;
          target.style.height = `${viewport.height / dpr}px`;
        }
        // Apply the scroll anchor computed by the wheel-zoom handler now
        // that the content has its new size.
        if (pendingScrollRef.current && wrapperRef.current) {
          wrapperRef.current.scrollLeft = pendingScrollRef.current.left;
          wrapperRef.current.scrollTop = pendingScrollRef.current.top;
          pendingScrollRef.current = null;
        }

        renderTaskRef.current?.cancel();
        const context = canvas.getContext("2d");
        if (!context) return;
        const task = page.render({ canvasContext: context, viewport });
        renderTaskRef.current = task;
        await task.promise;
        if (!cancelled) {
          setRender({ scale, dpr });
          setRenderError(null);
        }
      } catch (error) {
        if (isRenderingCancelled(error)) return;
        if (!cancelled) setRenderError(String(error));
      }
    })();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
    };
  }, [sheet.id, sheet.document_id, sheet.page_number, sheet.width, sheet.height, wrapperWidth, zoom]);

  // Draw verdict boxes. Big boxes first so small assets stay clickable and
  // visible on top; labels only where they help (selected, hovered, or
  // wide-enough boxes) to avoid the chip pile-up on dense plans. When an asset
  // is selected, the rest dim to a "spotlight" and the selection is redrawn
  // last, on top, with a glow so it pops regardless of the big-first z-order.
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay || !render) return;
    const ctx = overlay.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(render.dpr, 0, 0, render.dpr, 0, 0);
    ctx.clearRect(0, 0, overlay.width, overlay.height);

    const hasSelection = selectedAssetId != null;

    // Draw one asset. `dim` fades everything except the spotlight; `selected`
    // brightens the fill and adds a glow ring.
    const drawAsset = (asset: Asset, dim: boolean, selected: boolean) => {
      const verdict = worstBoxVerdict(asset.verdicts);
      const color = verdict ? verdictColor(verdict) : UNVERDICTED;
      const [x0, y0, x1, y1] = asset.bbox;
      const x = (x0 - BOX_PAD) * render.scale;
      const y = (y0 - BOX_PAD) * render.scale;
      const w = (x1 - x0 + BOX_PAD * 2) * render.scale;
      const h = (y1 - y0 + BOX_PAD * 2) * render.scale;
      const hovered = asset.id === hoverId && !selected;
      const dimFactor = dim ? 0.4 : 1;

      // Space-covering assets (rooms/corridors) span the drawing — filling
      // them all would wash the plan out, so they tint only when active.
      const spanning = asset.type === "room" || asset.type === "corridor";
      if (!spanning || selected || hovered) {
        ctx.globalAlpha = (selected ? 0.28 : hovered ? 0.14 : 0.07) * dimFactor;
        ctx.fillStyle = color;
        ctx.fillRect(x, y, w, h);
      }
      ctx.globalAlpha = dimFactor;
      if (selected) {
        ctx.shadowColor = color; // glow ring around the spotlight
        ctx.shadowBlur = 12;
      }
      ctx.lineWidth = selected ? 3 : hovered ? 2.25 : spanning ? 1 : 1.25;
      ctx.strokeStyle = color;
      if (spanning && !selected && !hovered) ctx.setLineDash([6, 4]);
      ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]);
      ctx.shadowBlur = 0;
      ctx.shadowColor = "transparent";

      // Full chip on select/hover/wide; otherwise a tiny type code so every
      // box still says what it is (fixes blank unnamed boxes). Name falls back
      // to the type word, and the key measurement rides alongside.
      const showChip = selected || hovered || w >= LABEL_MIN_WIDTH_PX;
      ctx.globalAlpha = dim ? 0.5 : 1;
      if (showChip) {
        const measure = assetPrimaryMeasure(asset);
        const text =
          (verdict ? `${verdictGlyph(verdict)} ` : "") +
          assetDisplayName(asset) +
          (measure ? ` · ${measure}` : "");
        ctx.font = `${selected || hovered ? "600 " : ""}11px ui-sans-serif, system-ui`;
        const chipW = ctx.measureText(text).width + 10;
        const chipH = 16;
        const inside = h > chipH * 2 && w > chipW + 8;
        const chipX = inside ? x + 3 : x;
        const chipY = inside ? y + 3 : Math.max(y - chipH - 2, 0);
        ctx.fillStyle = color;
        ctx.fillRect(chipX, chipY, chipW, chipH);
        ctx.fillStyle = "#0b0d12"; // --color-surface-0: dark text on the chip
        ctx.fillText(text, chipX + 5, chipY + 12);
      } else {
        const code = assetTypeCode(asset.type);
        ctx.font = "600 9px ui-sans-serif, system-ui";
        const badgeW = ctx.measureText(code).width + 6;
        ctx.fillStyle = color;
        ctx.fillRect(x, y, badgeW, 13);
        ctx.fillStyle = "#0b0d12"; // --color-surface-0
        ctx.fillText(code, x + 3, y + 9.5);
      }
      ctx.globalAlpha = 1;
    };

    const ordered = [...sheet.assets].sort((a, b) => boxArea(b) - boxArea(a));
    let selected: Asset | null = null;
    for (const asset of ordered) {
      if (isAssetHidden(asset, hidden)) continue; // toggled off in the legend
      if (asset.id === selectedAssetId) {
        selected = asset; // defer to the on-top pass
        continue;
      }
      // Dim the rest only while spotlighting, and keep a hovered box lit.
      drawAsset(asset, hasSelection && asset.id !== hoverId, false);
    }
    if (selected) drawAsset(selected, false, true);
  }, [sheet.assets, selectedAssetId, hoverId, render, hidden]);

  // Center the selected asset when it isn't already fully in view — so a
  // selection made from the clause pane visibly brings its box into frame.
  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper || !render || !selectedAssetId) return;
    const asset = sheet.assets.find((a) => a.id === selectedAssetId);
    if (!asset) return;
    const [x0, y0, x1, y1] = asset.bbox;
    const left = (x0 - BOX_PAD) * render.scale;
    const top = (y0 - BOX_PAD) * render.scale;
    const right = (x1 + BOX_PAD) * render.scale;
    const bottom = (y1 + BOX_PAD) * render.scale;
    const viewL = wrapper.scrollLeft;
    const viewT = wrapper.scrollTop;
    if (
      left >= viewL &&
      right <= viewL + wrapper.clientWidth &&
      top >= viewT &&
      bottom <= viewT + wrapper.clientHeight
    ) {
      return; // already in frame — don't yank the plan around
    }
    wrapper.scrollTo({
      left: Math.max(0, (left + right) / 2 - wrapper.clientWidth / 2),
      top: Math.max(0, (top + bottom) / 2 - wrapper.clientHeight / 2),
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  }, [selectedAssetId, render, sheet.assets]);

  const toSheetCoords = useCallback(
    (clientX: number, clientY: number) => {
      const overlay = overlayRef.current;
      if (!overlay || !render) return null;
      const rect = overlay.getBoundingClientRect();
      return {
        x: (clientX - rect.left) / render.scale,
        y: (clientY - rect.top) / render.scale,
      };
    },
    [render],
  );

  // Ctrl/Cmd + wheel zooms around the cursor (non-passive so we can prevent
  // the browser's page zoom); plain wheel keeps scrolling.
  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setZoom((current) => {
        const next = Math.min(
          ZOOM_MAX,
          Math.max(ZOOM_MIN, current * (event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP)),
        );
        if (next !== current) {
          const rect = wrapper.getBoundingClientRect();
          const offsetX = event.clientX - rect.left;
          const offsetY = event.clientY - rect.top;
          const ratio = next / current;
          pendingScrollRef.current = {
            left: (wrapper.scrollLeft + offsetX) * ratio - offsetX,
            top: (wrapper.scrollTop + offsetY) * ratio - offsetY,
          };
        }
        return next;
      });
    };
    wrapper.addEventListener("wheel", onWheel, { passive: false });
    return () => wrapper.removeEventListener("wheel", onWheel);
  }, []);

  // Drag anywhere to pan; a pointer that never travels past the threshold
  // is a click (selection).
  function onPointerDown(event: React.PointerEvent) {
    const wrapper = wrapperRef.current;
    if (!wrapper || event.button !== 0) return;
    dragRef.current = {
      active: true,
      moved: false,
      x: event.clientX,
      y: event.clientY,
      left: wrapper.scrollLeft,
      top: wrapper.scrollTop,
    };
  }

  function onPointerMove(event: React.PointerEvent) {
    const wrapper = wrapperRef.current;
    const drag = dragRef.current;
    if (wrapper && drag.active) {
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (drag.moved || Math.abs(dx) > DRAG_THRESHOLD_PX || Math.abs(dy) > DRAG_THRESHOLD_PX) {
        drag.moved = true;
        wrapper.scrollLeft = drag.left - dx;
        wrapper.scrollTop = drag.top - dy;
        wrapper.style.cursor = "grabbing";
        return;
      }
    }
    const point = toSheetCoords(event.clientX, event.clientY);
    if (!point) return;
    const top = hitsAt(sheet, point.x, point.y, hidden)[0] ?? null;
    setHoverId((prev) => (prev === (top?.id ?? null) ? prev : (top?.id ?? null)));
    if (wrapper) wrapper.style.cursor = top ? "pointer" : "default";
  }

  function onPointerUp() {
    const wrapper = wrapperRef.current;
    if (wrapper) wrapper.style.cursor = "default";
    // keep drag.moved until the click event has had a chance to read it
    setTimeout(() => {
      dragRef.current.active = false;
      dragRef.current.moved = false;
    }, 0);
  }

  function handleClick(event: React.MouseEvent<HTMLCanvasElement>) {
    if (dragRef.current.moved) return; // it was a pan, not a click
    const point = toSheetCoords(event.clientX, event.clientY);
    if (!point) return;
    const hits = hitsAt(sheet, point.x, point.y, hidden);
    if (hits.length === 0) {
      onSelectAsset(null);
      return;
    }
    // Repeated clicks on the same spot cycle through overlapping assets.
    const currentIndex = hits.findIndex((asset) => asset.id === selectedAssetId);
    onSelectAsset(hits[(currentIndex + 1) % hits.length]);
  }

  const zoomBy = (factor: number) =>
    setZoom((current) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, current * factor)));

  return (
    <div className="relative p-3">
      {renderError && (
        <p className="mb-2 rounded-lg border border-fail/40 bg-fail/10 px-3 py-2 text-fail">
          PDF render failed: {renderError}
        </p>
      )}

      <CanvasLegend assets={sheet.assets} hidden={hidden} onToggle={onToggleHidden} />

      {/* Zoom toolbar */}
      <div className="absolute top-5 right-5 z-10 flex items-center gap-1 rounded-lg border border-edge bg-surface-1/90 p-1 backdrop-blur">
        <button
          aria-label="Zoom out"
          onClick={() => zoomBy(1 / ZOOM_STEP)}
          className="rounded px-2 py-0.5 text-ink-dim hover:bg-surface-2 hover:text-ink"
        >
          −
        </button>
        <span className="min-w-11 text-center text-xs text-ink-dim tabular-nums">
          {Math.round(zoom * 100)}%
        </span>
        <button
          aria-label="Zoom in"
          onClick={() => zoomBy(ZOOM_STEP)}
          className="rounded px-2 py-0.5 text-ink-dim hover:bg-surface-2 hover:text-ink"
        >
          +
        </button>
        <button
          aria-label="Fit to pane"
          onClick={() => setZoom(1)}
          className="rounded px-2 py-0.5 text-xs text-ink-dim hover:bg-surface-2 hover:text-ink"
        >
          Fit
        </button>
      </div>

      <div
        ref={wrapperRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => {
          onPointerUp();
          setHoverId(null);
        }}
        className="max-h-[72vh] overflow-auto overscroll-contain rounded-md scrollbar-gutter-stable"
      >
        <div className="relative inline-block">
          <canvas ref={pdfCanvasRef} className="block rounded-md bg-white" />
          <canvas ref={overlayRef} onClick={handleClick} className="absolute inset-0" />
        </div>
      </div>
      <p className="mt-1.5 text-[11px] text-ink-dim">
        Ctrl+scroll to zoom · drag to pan · click overlapping assets again to
        cycle through them
      </p>
    </div>
  );
}
