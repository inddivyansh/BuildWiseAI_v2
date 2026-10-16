"use client";

import React from "react";
import { Download, Loader2 } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getExportFormats } from "@/lib/api";
import { useExportAction } from "@/hooks/useFloorPlanActions";
import { useFloorPlanStore } from "@/stores/floorPlanStore";

export function ExportMenu() {
  const { currentPlan } = useFloorPlanStore();
  const exportAction = useExportAction();

  const { data: formatsData } = useQuery({
    queryKey: ["export-formats"],
    queryFn: getExportFormats,
  });

  const backendFormats = formatsData?.formats ?? [
    { id: "svg", name: "SVG Vector", description: "Scalable vector graphics" },
    { id: "dxf", name: "AutoCAD DXF", description: "CAD drawing exchange format" },
  ];

  const formats = [
    { id: "pdf", name: "PDF Vector Sheet", description: "Architectural vector drawing with title block" },
    ...backendFormats,
  ];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="inline-flex items-center justify-center rounded-md h-7 px-3 text-xs glass border border-input hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
        disabled={!currentPlan}
      >
        <Download size={12} className="mr-1" />
        Export
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {formats.map((fmt) => (
          <DropdownMenuItem
            key={fmt.id}
            onClick={() => exportAction.mutate(fmt.id)}
            disabled={exportAction.isPending}
          >
            {exportAction.isPending &&
            exportAction.variables === fmt.id ? (
              <Loader2 size={14} className="mr-2 animate-spin" />
            ) : (
              <Download size={14} className="mr-2" />
            )}
            {fmt.name}
            <span className="ml-auto text-xs text-muted-foreground">
              .{fmt.id}
            </span>
          </DropdownMenuItem>
        ))}
        {formats.length === 0 && (
          <DropdownMenuItem disabled>No formats available</DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
