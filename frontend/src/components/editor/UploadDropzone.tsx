"use client";

import React, { useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { Upload, Loader2, FileUp } from "lucide-react";
import { useIngestAction } from "@/hooks/useFloorPlanActions";
import { api } from "@/lib/api";

const ACCEPTED_TYPES = [
  ".pdf",
  ".png",
  ".jpg",
  ".jpeg",
  ".dxf",
  ".ifc",
  ".tiff",
  ".bmp",
  ".webp",
];

const ACCEPTED_MIME = [
  "application/pdf",
  "image/png",
  "image/jpeg",
  "image/tiff",
  "image/bmp",
  "image/webp",
  "application/dxf",
  "application/octet-stream",
];

export function UploadDropzone({
  children,
  projectId,
}: {
  children: React.ReactNode;
  projectId?: string;
}) {
  const router = useRouter();
  const [isDragging, setIsDragging] = useState(false);
  const dragCounter = useRef(0);
  const ingestAction = useIngestAction();

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current += 1;
    if (dragCounter.current === 1) {
      setIsDragging(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current -= 1;
    if (dragCounter.current === 0) {
      setIsDragging(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);
      dragCounter.current = 0;

      const files = Array.from(e.dataTransfer.files);
      const file = files[0];
      if (!file) return;

      const ext = `.${file.name.split(".").pop()?.toLowerCase()}`;
      if (!ACCEPTED_TYPES.includes(ext)) {
        return;
      }

      let targetProjectId = projectId;
      if (!targetProjectId) {
        try {
          const newProj = await api.createProject(
            `Drawing: ${file.name}`,
            `Imported file: ${file.name}`
          );
          targetProjectId = newProj.id;
        } catch {
          // fallback
        }
      }

      ingestAction.mutate(
        { file, projectId: targetProjectId },
        {
          onSuccess: (data) => {
            if (data.floor_plan && targetProjectId) {
              router.replace(
                `/editor/${targetProjectId}?blueprintId=${data.floor_plan.generation_id}`
              );
            }
          },
        }
      );
    },
    [ingestAction, projectId, router],
  );

  return (
    <div
      className="relative w-full h-full"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {children}

      {/* Drop overlay */}
      {isDragging && (
        <div className="absolute inset-0 z-50 bg-background/80 backdrop-blur-sm flex items-center justify-center border-2 border-dashed border-foreground/30 rounded-xl m-2">
          <div className="text-center">
            {ingestAction.isPending ? (
              <>
                <Loader2 size={32} className="mx-auto mb-2 animate-spin text-muted-foreground" />
                <p className="text-sm text-muted-foreground">Parsing plan...</p>
              </>
            ) : (
              <>
                <FileUp size={32} className="mx-auto mb-2 text-muted-foreground" />
                <p className="text-sm font-medium">Drop file to upload</p>
                <p className="text-xs text-muted-foreground mt-1">
                  PDF, DXF, IFC, PNG, JPG, TIFF, BMP, WebP
                </p>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
