"use client";

import React, { useEffect, Suspense } from "react";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { Loader2 } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useFloorPlanStore } from "@/stores/floorPlanStore";
import { useEditorStore } from "@/stores/editorStore";
import { getProject, listBlueprints } from "@/lib/api";
import { Toolbar } from "@/components/editor/Toolbar";
import { PropertyPanel } from "@/components/editor/PropertyPanel";
import { NLInput } from "@/components/editor/NLInput";
import { UploadDropzone } from "@/components/editor/UploadDropzone";
import { CanvasOverlay } from "@/components/canvas/CanvasOverlay";

const FloorPlanCanvas = dynamic(
  () =>
    import("@/components/canvas/FloorPlanCanvas").then(
      (mod) => mod.FloorPlanCanvas,
    ),
  { ssr: false },
);

const ThreeViewer = dynamic(
  () =>
    import("@/components/viewer/ThreeViewer").then((mod) => mod.ThreeViewer),
  { ssr: false },
);

function EditorContent() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();

  const id = params.id;
  const projectId = id && id !== "new" ? id : undefined;
  const targetBlueprintId = searchParams.get("blueprintId") || undefined;

  const { currentPlan, setPlan, setCompliance, viewMode } =
    useFloorPlanStore();
  const { rightPanelOpen } = useEditorStore();

  // Fetch blueprints list for this project if an existing project
  const { data: blueprints = [] } = useQuery({
    queryKey: ["project-blueprints", projectId],
    queryFn: () => listBlueprints(projectId!),
    enabled: !!projectId,
  });

  // Fetch project and specific/latest blueprint
  const {
    data: project,
    isLoading: projectLoading,
    isError: projectError,
    error: projectErr,
  } = useQuery({
    queryKey: ["project", projectId, targetBlueprintId],
    queryFn: () => getProject(projectId!, targetBlueprintId),
    enabled: !!projectId,
    retry: 1,
  });

  // Hydrate store when project/blueprint loads
  useEffect(() => {
    if (project?.latest_floor_plan) {
      setPlan(project.latest_floor_plan);
      if (project.compliance) {
        setCompliance(project.compliance);
      }
    }
  }, [project, setPlan, setCompliance]);

  const handleSelectBlueprint = (blueprintId: string) => {
    router.push(`/editor/${projectId}?blueprintId=${blueprintId}`);
  };

  const handleNewBlueprint = () => {
    setPlan(null as any);
    router.push(`/editor/${projectId}`);
  };

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
      {/* Left: Tool strip */}
      <Toolbar projectId={projectId} />

      {/* Center: Canvas + overlays + NL input */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        <div className="flex-1 relative overflow-hidden">
          <UploadDropzone projectId={projectId}>
            <CanvasOverlay
              projectId={projectId}
              projectName={project?.name}
              blueprints={blueprints}
              currentBlueprintId={targetBlueprintId || (blueprints[0]?.id)}
              onSelectBlueprint={handleSelectBlueprint}
              onNewBlueprint={handleNewBlueprint}
            />

            {projectError ? (
              <div className="w-full h-full flex items-center justify-center p-6 bg-surface-0">
                <div className="max-w-md w-full p-6 rounded-xl border border-edge bg-surface-1 text-center shadow-lg">
                  <div className="w-12 h-12 rounded-xl bg-fail/15 border border-fail/30 flex items-center justify-center mx-auto mb-3 text-fail">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10" />
                      <line x1="12" y1="8" x2="12" y2="12" />
                      <line x1="12" y1="16" x2="12.01" y2="16" />
                    </svg>
                  </div>
                  <h3 className="text-base font-semibold text-ink">Project Not Found</h3>
                  <p className="text-xs text-ink-dim mt-1.5 mb-5">
                    {(projectErr as Error)?.message || "The requested project could not be found or you do not have permission to view it."}
                  </p>
                  <div className="flex items-center justify-center gap-2">
                    <button
                      onClick={() => router.push("/projects")}
                      className="px-3.5 py-1.5 rounded-lg border border-edge bg-surface-2 text-ink text-xs font-medium hover:bg-surface-2/80 transition-colors"
                    >
                      View All Projects
                    </button>
                    <button
                      onClick={() => router.push("/editor/new")}
                      className="px-3.5 py-1.5 rounded-lg bg-accent text-white text-xs font-medium hover:bg-accent/90 transition-colors"
                    >
                      Create New Blueprint
                    </button>
                  </div>
                </div>
              </div>
            ) : projectLoading && !currentPlan ? (
              <div className="w-full h-full flex flex-col items-center justify-center bg-surface-0 gap-3">
                <div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
                <p className="text-xs text-ink-dim font-medium">Loading project blueprints...</p>
              </div>
            ) : viewMode === "2d" ? (
              currentPlan ? (
                <FloorPlanCanvas />
              ) : (
                <EmptyCanvas hasProject={!!projectId} projectName={project?.name} />
              )
            ) : currentPlan ? (
              <ThreeViewer />
            ) : (
              <EmptyCanvas hasProject={!!projectId} projectName={project?.name} />
            )}
          </UploadDropzone>
        </div>

        <NLInput projectId={projectId} />
      </div>

      {/* Right: Property panel */}
      {rightPanelOpen && <PropertyPanel projectId={projectId} />}
    </div>
  );
}

function EmptyCanvas({ hasProject, projectName }: { hasProject?: boolean; projectName?: string }) {
  return (
    <div className="w-full h-full flex items-center justify-center bg-[radial-gradient(#ffffff08_1px,transparent_1px)] [background-size:40px_40px]">
      <div className="text-center max-w-sm px-4">
        <div className="w-12 h-12 rounded-xl bg-muted/50 flex items-center justify-center mx-auto mb-3">
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            className="text-muted-foreground/40"
          >
            <rect
              x="3"
              y="3"
              width="18"
              height="18"
              rx="2"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeDasharray="4 2"
            />
            <path d="M3 9h18M9 3v18" stroke="currentColor" strokeWidth="1" />
          </svg>
        </div>
        <p className="text-sm font-medium text-foreground">
          {hasProject
            ? projectName
              ? `No floor plan in "${projectName}" yet`
              : "No floor plans in this project yet"
            : "Blueprint Studio Ready"}
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          Enter a prompt below to generate a new layout, or import a drawing (PDF/CAD) from the toolbar.
        </p>
      </div>
    </div>
  );
}

export default function EditorPage() {
  return (
    <Suspense
      fallback={
        <div className="h-screen w-screen bg-background flex items-center justify-center">
          <Loader2 size={24} className="animate-spin text-accent" />
        </div>
      }
    >
      <EditorContent />
    </Suspense>
  );
}
