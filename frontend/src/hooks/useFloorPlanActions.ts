"use client";

import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { useFloorPlanStore } from "@/stores/floorPlanStore";
import {
  generateFloorPlan,
  editFloorPlan,
  checkCompliance,
  exportPlan,
  ingestFile,
  sendBlueprintToComplianceAudit,
  api,
} from "@/lib/api";
import type { GenerateRequest, EditRequest } from "@/types/floorPlan";

// ---------------------------------------------------------------------------
// Generate
// ---------------------------------------------------------------------------

export function useGenerateAction() {
  const { setPlan, setCompliance, setGenerating, setGenerationError } =
    useFloorPlanStore();

  return useMutation({
    mutationFn: (req: GenerateRequest) => {
      setGenerating(true);
      return generateFloorPlan(req);
    },
    onSuccess: (data) => {
      if (data.floor_plan) {
        setPlan(data.floor_plan);
      }
      if (data.compliance) {
        setCompliance(data.compliance);
      }
      if (data.status === "failed") {
        setGenerationError(data.error ?? "Generation failed");
        toast.error(data.error ?? "Generation failed");
      } else {
        toast.success("Floor plan generated");
      }
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : "Generation failed";
      setGenerationError(msg);
      toast.error(msg);
    },
  });
}

// ---------------------------------------------------------------------------
// Edit
// ---------------------------------------------------------------------------

export function useEditAction() {
  const { setPlan, setGenerating, setGenerationError } = useFloorPlanStore();

  return useMutation({
    mutationFn: (req: EditRequest) => {
      setGenerating(true);
      return editFloorPlan(req);
    },
    onSuccess: (data) => {
      if (data.floor_plan) {
        setPlan(data.floor_plan);
        toast.success(data.changes_summary ?? "Plan updated");
      }
      if (data.status === "failed") {
        setGenerationError(data.error ?? "Edit failed");
        toast.error(data.error ?? "Edit failed");
      }
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : "Edit failed";
      setGenerationError(msg);
      toast.error(msg);
    },
  });
}

// ---------------------------------------------------------------------------
// Compliance
// ---------------------------------------------------------------------------

export function useComplianceCheck() {
  const { currentPlan, setCompliance } = useFloorPlanStore();

  return useMutation({
    mutationFn: (opts?: { zone_id?: string }) => {
      if (!currentPlan) throw new Error("No floor plan loaded");
      return checkCompliance({
        floor_plan: currentPlan,
        zone_id: opts?.zone_id,
      });
    },
    onSuccess: (data) => {
      setCompliance(data);
      if (data.is_compliant) {
        toast.success("All compliance checks passed");
      } else {
        toast.warning(`${data.violations.length} compliance issue(s) found`);
      }
    },
    onError: (err) => {
      toast.error(
        err instanceof Error ? err.message : "Compliance check failed",
      );
    },
  });
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export function useExportAction() {
  const { currentPlan } = useFloorPlanStore();

  return useMutation({
    mutationFn: async (format: string) => {
      if (!currentPlan) throw new Error("No floor plan loaded");
      let blob: Blob;
      if (format === "pdf") {
        const { createFloorPlanPdfBlob } = await import("@/lib/pdfExport");
        blob = createFloorPlanPdfBlob(currentPlan);
      } else {
        blob = await exportPlan(currentPlan.generation_id, format);
      }
      // Browser download
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `floorplan_${currentPlan.generation_id.slice(0, 8)}.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return format;
    },
    onSuccess: (format) => {
      toast.success(`Exported as ${format.toUpperCase()}`);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Export failed");
    },
  });
}

// ---------------------------------------------------------------------------
// Ingest
// ---------------------------------------------------------------------------

export function useIngestAction() {
  const { setPlan, setGenerating, setGenerationError } = useFloorPlanStore();

  return useMutation({
    mutationFn: (args: File | { file: File; projectId?: string }) => {
      setGenerating(true);
      if (args instanceof File) {
        return ingestFile(args);
      }
      return ingestFile(args.file, { project_id: args.projectId });
    },
    onSuccess: (data) => {
      if (data.floor_plan) {
        setPlan(data.floor_plan);
        toast.success("Plan parsed successfully");
      }
      if (data.warnings.length > 0) {
        data.warnings.forEach((w) => toast.warning(w));
      }
      if (data.confidence != null && data.confidence < 0.5) {
        toast.warning("Low confidence detection — review results carefully");
      }
      if (data.status === "failed") {
        setGenerationError(data.error ?? "Ingestion failed");
        toast.error(data.error ?? "Ingestion failed");
      }
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : "Upload failed";
      setGenerationError(msg);
      toast.error(msg);
    },
  });
}

// ---------------------------------------------------------------------------
// Send to PlanLint Compliance Audit
// ---------------------------------------------------------------------------

export function useSendToAuditAction() {
  const router = useRouter();
  const { currentPlan } = useFloorPlanStore();

  return useMutation({
    mutationFn: async ({
      projectId,
      projectName,
    }: {
      projectId?: string;
      projectName?: string;
    }) => {
      if (!currentPlan) throw new Error("No floor plan available to audit");

      let targetProjectId = projectId;
      // If no project exists yet (e.g. editor/new), create one automatically
      if (!targetProjectId || targetProjectId === "new") {
        const title = projectName || `Blueprint Plan ${new Date().toLocaleDateString()}`;
        const newProj = await api.createProject(
          title,
          "Auto-created from Blueprint Studio for compliance verification",
        );
        targetProjectId = newProj.id;
      }

      const res = await sendBlueprintToComplianceAudit(
        targetProjectId,
        currentPlan,
        projectName || "Generated Blueprint",
      );

      return { projectId: targetProjectId, documentId: res.document_id };
    },
    onSuccess: (data) => {
      toast.success(
        "Blueprint exported as PDF and registered as compliance document",
      );
      router.push(`/project/${data.projectId}`);
    },
    onError: (err) => {
      const msg =
        err instanceof Error ? err.message : "Failed to send blueprint to audit";
      toast.error(msg);
    },
  });
}
