"use client";

import React, { useState, useRef, useEffect } from "react";
import { Upload, FileUp, Loader2, CheckCircle2, FolderKanban, Plus } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ingestFile, api } from "@/lib/api";
import type { ProjectSummary } from "@/lib/types";
import { toast } from "sonner";

interface UploadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId?: string;
  onUploadSuccess?: (generationId: string, projectId: string) => void;
}

const SUPPORTED_EXTS = [".pdf", ".dxf", ".ifc", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"];

export function UploadDialog({
  open,
  onOpenChange,
  projectId,
  onUploadSuccess,
}: UploadDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Project selection state when not pre-scoped to a project
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("new");
  const [newProjectName, setNewProjectName] = useState<string>("");

  useEffect(() => {
    if (open && !projectId) {
      api
        .listProjects()
        .then((projs) => {
          setProjects(projs);
          if (projs.length > 0) {
            setSelectedProjectId(projs[0].id);
          } else {
            setSelectedProjectId("new");
          }
        })
        .catch(() => setProjects([]));
    }
  }, [open, projectId]);

  function handleFileSelected(selected: File | null) {
    if (!selected) return;
    const ext = `.${selected.name.split(".").pop()?.toLowerCase()}`;
    if (!SUPPORTED_EXTS.includes(ext)) {
      toast.error(`Unsupported format. Supported: ${SUPPORTED_EXTS.join(", ")}`);
      return;
    }
    setFile(selected);
    if (!newProjectName) {
      const baseName = selected.name.replace(/\.[^/.]+$/, "");
      setNewProjectName(`Drawing: ${baseName}`);
    }
  }

  async function handleUpload() {
    if (!file) return;
    setIsUploading(true);
    try {
      let targetProjectId = projectId;

      if (!targetProjectId) {
        if (selectedProjectId === "new" || !selectedProjectId) {
          const projName = newProjectName.trim() || `Drawing: ${file.name}`;
          const newProj = await api.createProject(
            projName,
            `Imported floor plan from ${file.name}`
          );
          targetProjectId = newProj.id;
        } else {
          targetProjectId = selectedProjectId;
        }
      }

      const res = await ingestFile(file, { project_id: targetProjectId });
      if (res.status === "failed" || !res.floor_plan) {
        toast.error(res.error || "Failed to extract floor plan from file");
      } else {
        toast.success(`Floor plan parsed successfully (${res.floor_plan.spec?.rooms?.length || 0} rooms detected)`);
        onOpenChange(false);
        setFile(null);
        if (onUploadSuccess) {
          onUploadSuccess(res.generation_id, targetProjectId);
        }
      }
    } catch (err: any) {
      toast.error(err.message || "Failed to upload and parse floor plan");
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md bg-surface-1 border-edge text-ink">
        <DialogHeader>
          <DialogTitle className="text-base font-semibold text-ink">Import Floor Plan</DialogTitle>
          <DialogDescription className="text-xs text-ink-dim">
            Upload an existing architectural drawing (PDF, AutoCAD DXF, IFC, or raster image) to convert it into an interactive editable floor plan.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-2">
          {/* Target Project (if not scoped to a specific project) */}
          {!projectId && (
            <div className="space-y-2 p-3 bg-surface-2/60 rounded-lg border border-edge">
              <Label className="text-xs font-medium text-ink flex items-center gap-1.5">
                <FolderKanban size={13} className="text-accent" />
                Target Project
              </Label>
              <Select
                value={selectedProjectId}
                onValueChange={(val) => setSelectedProjectId(val || "new")}
              >
                <SelectTrigger className="bg-surface-1 border-edge text-ink text-xs h-8">
                  <SelectValue placeholder="Select destination project" />
                </SelectTrigger>
                <SelectContent className="bg-surface-1 border-edge text-ink">
                  <SelectItem value="new" className="text-xs">
                    + Create New Project
                  </SelectItem>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id} className="text-xs">
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {selectedProjectId === "new" && (
                <div className="pt-1.5">
                  <Input
                    placeholder="New project name..."
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    className="bg-surface-1 border-edge text-ink text-xs h-8"
                  />
                </div>
              )}
            </div>
          )}

          {/* Drop area */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragActive(false);
              const dropped = e.dataTransfer.files?.[0];
              if (dropped) handleFileSelected(dropped);
            }}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
              dragActive
                ? "border-accent bg-accent/10"
                : file
                ? "border-pass/60 bg-pass/5"
                : "border-edge hover:border-edge/80 hover:bg-surface-2/40"
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.dxf,.ifc,.png,.jpg,.jpeg,.tiff,.bmp,.webp"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleFileSelected(f);
                e.target.value = "";
              }}
            />

            {file ? (
              <div className="space-y-2">
                <CheckCircle2 size={32} className="mx-auto text-pass" />
                <p className="text-xs font-semibold text-ink truncate max-w-xs mx-auto">
                  {file.name}
                </p>
                <p className="text-[11px] text-ink-dim">
                  {(file.size / (1024 * 1024)).toFixed(2)} MB · Ready to parse
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                <FileUp size={32} className="mx-auto text-ink-dim/60" />
                <p className="text-xs font-medium text-ink">
                  Click or drag and drop to upload
                </p>
                <p className="text-[11px] text-ink-dim">
                  PDF, DXF, IFC, PNG, JPG, TIFF, BMP, WebP
                </p>
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setFile(null);
                onOpenChange(false);
              }}
              disabled={isUploading}
              className="border-edge bg-surface-2 text-ink hover:bg-surface-2/80 text-xs"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleUpload}
              disabled={!file || isUploading}
              className="bg-accent hover:bg-accent/90 text-white text-xs px-4"
            >
              {isUploading ? (
                <>
                  <Loader2 size={14} className="mr-1.5 animate-spin" />
                  Parsing Drawing...
                </>
              ) : (
                <>
                  <Upload size={14} className="mr-1.5" />
                  Parse & Import
                </>
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
