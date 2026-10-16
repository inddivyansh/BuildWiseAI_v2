"use client";

import React from "react";
import Link from "next/link";
import { Undo2, Redo2, ZoomIn, ZoomOut, FileCheck2, Loader2, Plus, ChevronRight, Layers } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useFloorPlanStore } from "@/stores/floorPlanStore";
import { useEditorStore } from "@/stores/editorStore";
import { ExportMenu } from "@/components/editor/ExportMenu";
import { useSendToAuditAction } from "@/hooks/useFloorPlanActions";

export interface CanvasOverlayProps {
  projectId?: string;
  projectName?: string;
  blueprints?: { id: string; generator_model: string; total_area_sqm: number; created_at: string }[];
  currentBlueprintId?: string;
  onSelectBlueprint?: (blueprintId: string) => void;
  onNewBlueprint?: () => void;
}

export function CanvasOverlay({
  projectId,
  projectName,
  blueprints = [],
  currentBlueprintId,
  onSelectBlueprint,
  onNewBlueprint,
}: CanvasOverlayProps = {}) {
  const { currentPlan, compliance, undo, redo, viewMode, setViewMode, historyIndex, history } =
    useFloorPlanStore();
  const { zoom, setZoom } = useEditorStore();
  const sendToAudit = useSendToAuditAction();

  const canUndo = historyIndex > 0;
  const canRedo = historyIndex < history.length - 1;

  return (
    <>
      {/* Top bar */}
      <div className="absolute top-3 left-3 right-3 z-10 flex flex-wrap items-center justify-between gap-2 pointer-events-none">
        {/* Left: Project breadcrumb + blueprint switcher + undo/redo + 2D/3D toggle */}
        <div className="flex items-center gap-2 pointer-events-auto flex-wrap">
          {/* Project & Blueprint Navigator */}
          {projectId && (
            <div className="flex items-center gap-1.5 glass rounded-lg px-2.5 py-1 text-xs">
              <Link href="/projects" className="text-muted-foreground hover:text-foreground">
                Projects
              </Link>
              <ChevronRight size={12} className="text-muted-foreground/60" />
              <Link
                href={`/project/${projectId}`}
                className="font-medium text-foreground hover:text-pass truncate max-w-[140px]"
                title="View Project Compliance Workspace"
              >
                {projectName || projectId}
              </Link>

              {/* Blueprints dropdown if multiple exist */}
              {blueprints.length > 0 && (
                <>
                  <span className="text-muted-foreground/40 mx-1">|</span>
                  <div className="flex items-center gap-1">
                    <Layers size={12} className="text-accent" />
                    <select
                      value={currentBlueprintId || (blueprints[0]?.id ?? "")}
                      onChange={(e) => onSelectBlueprint?.(e.target.value)}
                      className="bg-transparent border-none text-[11px] text-foreground font-medium outline-none cursor-pointer pr-1"
                    >
                      {blueprints.map((bp) => (
                        <option key={bp.id} value={bp.id} className="bg-surface-1 text-ink">
                          Plan {bp.id.slice(0, 8)} ({bp.total_area_sqm ? `${bp.total_area_sqm.toFixed(0)}m²` : "spec"})
                        </option>
                      ))}
                    </select>
                  </div>
                </>
              )}

              {onNewBlueprint && (
                <button
                  onClick={onNewBlueprint}
                  className="ml-1 text-[11px] text-accent hover:underline flex items-center gap-0.5"
                  title="Generate a new blueprint in this project"
                >
                  <Plus size={11} />
                  <span>New</span>
                </button>
              )}
            </div>
          )}

          {/* Undo / Redo */}
          <div className="flex items-center gap-0.5 glass rounded-lg p-0.5">
            <Tooltip>
              <TooltipTrigger
                className="inline-flex items-center justify-center rounded-md h-7 w-7 hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
                disabled={!canUndo}
                onClick={undo}
              >
                <Undo2 size={14} />
              </TooltipTrigger>
              <TooltipContent>Undo (Ctrl+Z)</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger
                className="inline-flex items-center justify-center rounded-md h-7 w-7 hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
                disabled={!canRedo}
                onClick={redo}
              >
                <Redo2 size={14} />
              </TooltipTrigger>
              <TooltipContent>Redo (Ctrl+Shift+Z)</TooltipContent>
            </Tooltip>
          </div>

          {/* 2D / 3D toggle pill */}
          <div className="flex items-center gap-0.5 p-0.5 glass rounded-full">
            <button
              className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                viewMode === "2d"
                  ? "bg-foreground text-background shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setViewMode("2d")}
            >
              2D
            </button>
            <button
              className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                viewMode === "3d"
                  ? "bg-foreground text-background shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setViewMode("3d")}
            >
              3D
            </button>
          </div>
        </div>

        {/* Right: compliance badge + send to audit + export + zoom */}
        <div className="flex items-center gap-2 pointer-events-auto">
          {compliance && (
            <Badge
              variant={compliance.is_compliant ? "default" : "destructive"}
              className="text-xs"
              title="Generation-time zoning and lot coverage check"
            >
              {compliance.is_compliant ? "Zoning Pass" : `${compliance.violations.length} Zoning Issue${compliance.violations.length === 1 ? "" : "s"}`}
            </Badge>
          )}

          {/* Direct "Send to Compliance Audit" Button */}
          {currentPlan && (
            <Button
              size="sm"
              onClick={() => sendToAudit.mutate({ projectId, projectName })}
              disabled={sendToAudit.isPending}
              className="bg-pass hover:bg-pass/90 text-surface-0 font-medium text-xs h-7 px-3 shadow-sm flex items-center gap-1.5 transition-colors"
              title="Export blueprint to vector PDF and open in PlanLint compliance audit"
            >
              {sendToAudit.isPending ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <FileCheck2 size={13} />
              )}
              <span>{sendToAudit.isPending ? "Sending..." : "Send to Compliance Audit"}</span>
            </Button>
          )}

          {currentPlan && <ExportMenu />}

          <span className="glass rounded-md px-2 py-1 text-xs tabular-nums text-muted-foreground">
            {Math.round(zoom * 100)}%
          </span>
        </div>
      </div>

      {/* Bottom-right: zoom buttons */}
      <div className="absolute bottom-3 right-3 z-10 flex flex-col gap-1 pointer-events-auto">
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8 glass"
          onClick={() => setZoom(zoom * 1.2)}
        >
          <ZoomIn size={14} />
        </Button>
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8 glass"
          onClick={() => setZoom(zoom / 1.2)}
        >
          <ZoomOut size={14} />
        </Button>
      </div>
    </>
  );
}
