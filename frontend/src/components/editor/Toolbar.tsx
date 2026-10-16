"use client";

import React, { useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Layers,
  MousePointer2,
  Hand,
  Ruler,
  Grid3X3,
  RulerIcon,
  SquareDashedBottom,
  Upload,
  Settings,
  FileCheck2,
} from "lucide-react";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useEditorStore, type EditorTool } from "@/stores/editorStore";
import { useIngestAction } from "@/hooks/useFloorPlanActions";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const tools: { id: EditorTool; icon: typeof MousePointer2; label: string; shortcut: string }[] = [
  { id: "select", icon: MousePointer2, label: "Select", shortcut: "V" },
  { id: "pan", icon: Hand, label: "Pan", shortcut: "H" },
  { id: "measure", icon: Ruler, label: "Measure", shortcut: "M" },
];

const btnBase = "inline-flex items-center justify-center rounded-md h-9 w-9 transition-colors";
const btnGhost = "hover:bg-accent hover:text-accent-foreground";
const btnActive = "bg-secondary text-secondary-foreground";

export function Toolbar({ projectId }: { projectId?: string }) {
  const router = useRouter();
  const { activeTool, setTool, showGrid, showDimensions, showSetbacks, toggleGrid, toggleDimensions, toggleSetbacks } =
    useEditorStore();
  const fileRef = useRef<HTMLInputElement>(null);
  const ingestMutation = useIngestAction();

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      let targetProjectId = projectId;
      if (!targetProjectId) {
        try {
          const newProj = await api.createProject(`Drawing: ${file.name}`, `Imported file: ${file.name}`);
          targetProjectId = newProj.id;
        } catch {
          // fallback
        }
      }
      ingestMutation.mutate(
        { file, projectId: targetProjectId },
        {
          onSuccess: (data) => {
            if (data.floor_plan && targetProjectId) {
              router.replace(`/editor/${targetProjectId}?blueprintId=${data.floor_plan.generation_id}`);
            }
          },
        },
      );
    }
    e.target.value = "";
  };

  return (
    <aside className="w-14 flex flex-col items-center py-3 border-r border-border/50 bg-background/80 shrink-0">
      {/* Brand -> Back to Dashboard */}
      <Tooltip>
        <TooltipTrigger
          render={
            <Link
              href="/"
              className="p-2 bg-foreground text-background hover:bg-accent hover:text-white rounded-lg mb-3 transition-colors"
            />
          }
        >
          <Layers size={18} />
        </TooltipTrigger>
        <TooltipContent side="right">Dashboard</TooltipContent>
      </Tooltip>

      {/* Compliance Audit Link if project exists */}
      {projectId && (
        <Tooltip>
          <TooltipTrigger
            render={
              <Link
                href={`/project/${projectId}`}
                className={cn(btnBase, "text-pass hover:bg-pass/15 mb-2")}
              />
            }
          >
            <FileCheck2 size={16} />
          </TooltipTrigger>
          <TooltipContent side="right">Check Building Compliance</TooltipContent>
        </Tooltip>
      )}

      <Separator className="w-8 mb-3" />

      {/* Tools */}
      <div className="flex flex-col gap-1">
        {tools.map((tool) => (
          <Tooltip key={tool.id}>
            <TooltipTrigger
              className={cn(btnBase, activeTool === tool.id ? btnActive : btnGhost)}
              onClick={() => setTool(tool.id)}
            >
              <tool.icon size={16} />
            </TooltipTrigger>
            <TooltipContent side="right">
              {tool.label} ({tool.shortcut})
            </TooltipContent>
          </Tooltip>
        ))}
      </div>

      <Separator className="w-8 my-3" />

      {/* Toggles */}
      <div className="flex flex-col gap-1">
        <Tooltip>
          <TooltipTrigger
            className={cn(btnBase, showGrid ? btnActive : btnGhost)}
            onClick={toggleGrid}
          >
            <Grid3X3 size={16} />
          </TooltipTrigger>
          <TooltipContent side="right">Grid</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger
            className={cn(btnBase, showDimensions ? btnActive : btnGhost)}
            onClick={toggleDimensions}
          >
            <RulerIcon size={16} />
          </TooltipTrigger>
          <TooltipContent side="right">Dimensions</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger
            className={cn(btnBase, showSetbacks ? btnActive : btnGhost)}
            onClick={toggleSetbacks}
          >
            <SquareDashedBottom size={16} />
          </TooltipTrigger>
          <TooltipContent side="right">Setbacks</TooltipContent>
        </Tooltip>
      </div>

      <Separator className="w-8 my-3" />

      {/* Upload */}
      <Tooltip>
        <TooltipTrigger
          className={cn(btnBase, btnGhost)}
          onClick={() => fileRef.current?.click()}
        >
          <Upload size={16} />
        </TooltipTrigger>
        <TooltipContent side="right">Upload Plan</TooltipContent>
      </Tooltip>

      <input
        ref={fileRef}
        type="file"
        accept=".pdf,.png,.jpg,.jpeg,.dxf,.ifc,.tiff,.bmp,.webp"
        className="hidden"
        onChange={handleUpload}
      />

      {/* Bottom: settings */}
      <div className="mt-auto">
        <Tooltip>
          <TooltipTrigger className={cn(btnBase, btnGhost)}>
            <Settings size={16} />
          </TooltipTrigger>
          <TooltipContent side="right">Settings</TooltipContent>
        </Tooltip>
      </div>
    </aside>
  );
}
