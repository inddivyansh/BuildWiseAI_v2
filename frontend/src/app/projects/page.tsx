"use client";

import React, { useCallback, useEffect, useState, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Building2,
  FileCheck2,
  Layers,
  Plus,
  Trash2,
  Clock,
  FileText,
  Search,
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  CheckCircle2,
  AlertTriangle,
  Play,
  FileCode,
  Loader2,
} from "lucide-react";

import { Navbar } from "@/components/Navbar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import ConfirmDialog, { type ConfirmRequest } from "@/components/ConfirmDialog";
import { api, type BlueprintSummary } from "@/lib/api";
import type { ProjectSummary } from "@/lib/types";
import { toast } from "sonner";

function relativeDate(iso?: string): string {
  if (!iso) return "recently";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "recently";
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  return new Date(iso).toLocaleDateString();
}

function ProjectsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialTab = searchParams.get("tab") || "all";

  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState<"all" | "compliance" | "blueprints">(
    initialTab === "compliance" ? "compliance" : "all"
  );

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectDesc, setNewProjectDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);

  const loadProjects = useCallback(() => {
    api
      .listProjects()
      .then((data) => {
        setProjects(data);
        setLoading(false);
      })
      .catch((err) => {
        toast.error(`Failed to load projects: ${err.message || err}`);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  async function handleCreateProject() {
    if (!newProjectName.trim()) return;
    setBusy(true);
    try {
      const proj = await api.createProject(newProjectName.trim(), newProjectDesc.trim() || undefined);
      toast.success("Project created");
      setCreateModalOpen(false);
      setNewProjectName("");
      setNewProjectDesc("");
      router.push(`/project/${proj.id}`);
    } catch (e: any) {
      toast.error(e.message || "Failed to create project");
    } finally {
      setBusy(false);
    }
  }

  function handleDelete(id: string, name: string) {
    setConfirm({
      title: `Delete "${name}"?`,
      body: "All documents, sheets, generated blueprints, and verification results will be permanently removed.",
      confirmLabel: "Delete Project",
      onConfirm: async () => {
        try {
          await api.deleteProject(id);
          toast.success("Project deleted");
          loadProjects();
        } catch (e: any) {
          toast.error(e.message || "Failed to delete project");
        }
      },
    });
  }

  const filtered = projects.filter((p) => {
    const matchesSearch =
      !search.trim() ||
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      (p.description && p.description.toLowerCase().includes(search.toLowerCase()));

    if (!matchesSearch) return false;
    if (activeTab === "compliance") return (p.document_count ?? 0) > 0;
    if (activeTab === "blueprints") return (p.blueprint_count ?? 0) > 0;
    return true;
  });

  return (
    <div className="min-h-screen bg-surface-0 flex flex-col">
      <Navbar />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Top Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Link
                href="/"
                className="text-xs text-ink-dim hover:text-ink flex items-center gap-1"
              >
                <ArrowLeft size={13} />
                Dashboard
              </Link>
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-ink font-sans">
              Projects Management
            </h1>
            <p className="text-xs text-ink-dim mt-0.5">
              Access compliance audit results and generated floor plans across all persistent projects.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button
              onClick={() => setCreateModalOpen(true)}
              size="sm"
              className="bg-accent hover:bg-accent/90 text-white text-xs h-9 px-3.5"
            >
              <Plus size={15} className="mr-1.5" />
              New Project
            </Button>
          </div>
        </div>

        {/* Filter Bar & Tabs */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-edge mb-6">
          <div className="flex items-center gap-1 p-1 bg-surface-1 rounded-lg border border-edge">
            <button
              onClick={() => setActiveTab("all")}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                activeTab === "all"
                  ? "bg-surface-2 text-ink shadow-sm"
                  : "text-ink-dim hover:text-ink"
              }`}
            >
              All Projects ({projects.length})
            </button>
            <button
              onClick={() => setActiveTab("compliance")}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                activeTab === "compliance"
                  ? "bg-surface-2 text-pass shadow-sm"
                  : "text-ink-dim hover:text-ink"
              }`}
            >
              Compliance Audits (
              {projects.filter((p) => (p.document_count ?? 0) > 0).length})
            </button>
            <button
              onClick={() => setActiveTab("blueprints")}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                activeTab === "blueprints"
                  ? "bg-surface-2 text-accent shadow-sm"
                  : "text-ink-dim hover:text-ink"
              }`}
            >
              Blueprints (
              {projects.filter((p) => (p.blueprint_count ?? 0) > 0).length})
            </button>
          </div>

          <div className="relative w-full sm:w-64">
            <Search size={14} className="absolute left-3 top-2.5 text-ink-dim" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search projects by name..."
              className="bg-surface-1 border-edge text-ink pl-8 text-xs h-9"
            />
          </div>
        </div>

        {/* Grid */}
        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} className="h-44 rounded-xl bg-surface-1/40 border border-edge animate-pulse" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="border border-dashed border-edge rounded-xl py-16 px-4 text-center bg-surface-1/20">
            <Building2 size={36} className="mx-auto text-ink-dim/40 mb-3" />
            <h3 className="text-sm font-semibold text-ink">No projects in this view</h3>
            <p className="text-xs text-ink-dim max-w-sm mx-auto mt-1 mb-4">
              {search
                ? `No projects matching "${search}"`
                : "Create a new project to start an audit or generate floor plans."}
            </p>
            <Button
              size="sm"
              onClick={() => setCreateModalOpen(true)}
              className="bg-accent hover:bg-accent/90 text-white text-xs"
            >
              <Plus size={14} className="mr-1.5" />
              Create Project
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {filtered.map((project) => (
              <ProjectItemCard
                key={project.id}
                project={project}
                onDelete={() => handleDelete(project.id, project.name)}
                onProjectUpdated={loadProjects}
              />
            ))}
          </div>
        )}
      </main>

      {/* Create Modal */}
      {createModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-surface-1 border border-edge rounded-xl max-w-md w-full p-6 shadow-2xl">
            <h3 className="text-base font-semibold text-ink mb-1">Create New Project</h3>
            <p className="text-xs text-ink-dim mb-4">
              A persistent workspace for compliance checking and blueprint design.
            </p>
            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-ink mb-1 block">Project Name</label>
                <Input
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  placeholder="e.g. Skyline Towers Block B"
                  className="bg-surface-2 border-edge text-ink text-xs"
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs font-medium text-ink mb-1 block">Description (Optional)</label>
                <Input
                  value={newProjectDesc}
                  onChange={(e) => setNewProjectDesc(e.target.value)}
                  placeholder="e.g. ADA accessibility audit & 3-bedroom residential layouts"
                  className="bg-surface-2 border-edge text-ink text-xs"
                />
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCreateModalOpen(false)}
                className="border-edge text-ink text-xs"
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={handleCreateProject}
                disabled={busy || !newProjectName.trim()}
                className="bg-accent hover:bg-accent/90 text-white text-xs"
              >
                {busy ? "Creating..." : "Create Project"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Confirmation Dialog */}
      <ConfirmDialog request={confirm} onClose={() => setConfirm(null)} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Interactive Project Item Card with Expandable Blueprints & Audit History
// ---------------------------------------------------------------------------

function ProjectItemCard({
  project,
  onDelete,
  onProjectUpdated,
}: {
  project: ProjectSummary;
  onDelete: () => void;
  onProjectUpdated: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<"blueprints" | "audit">("blueprints");
  const [blueprints, setBlueprints] = useState<BlueprintSummary[]>([]);
  const [documents, setDocuments] = useState<any[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [loadingDetails, setLoadingDetails] = useState(false);

  const fetchDetails = useCallback(async () => {
    setLoadingDetails(true);
    try {
      const [bps, docs, runList] = await Promise.all([
        api.listBlueprints(project.id).catch(() => []),
        api.listDocuments(project.id).catch(() => []),
        api.listRuns(project.id).catch(() => []),
      ]);
      setBlueprints(bps);
      setDocuments(docs);
      setRuns(runList);
    } finally {
      setLoadingDetails(false);
    }
  }, [project.id]);

  useEffect(() => {
    if (expanded) {
      fetchDetails();
    }
  }, [expanded, fetchDetails]);

  async function handleDeleteBlueprint(blueprintId: string) {
    try {
      await api.deleteBlueprint(project.id, blueprintId);
      toast.success("Blueprint removed");
      fetchDetails();
      onProjectUpdated();
    } catch (e: any) {
      toast.error(e.message || "Failed to delete blueprint");
    }
  }

  return (
    <Card className="border-edge bg-surface-1 hover:border-edge/90 transition-all flex flex-col justify-between p-4 group">
      <div>
        <div className="flex items-start justify-between gap-2 mb-1.5">
          <h3 className="font-semibold text-sm text-ink truncate group-hover:text-accent transition-colors">
            {project.name}
          </h3>
          <button
            onClick={onDelete}
            className="text-ink-dim hover:text-fail p-1 rounded transition-colors opacity-60 hover:opacity-100"
            title="Delete project"
          >
            <Trash2 size={13} />
          </button>
        </div>

        <p className="text-xs text-ink-dim line-clamp-2 min-h-[32px] mb-3">
          {project.description || "No project description provided."}
        </p>

        <div className="flex items-center justify-between text-[11px] text-ink-dim mb-3 pb-3 border-b border-edge/40">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <Clock size={12} />
              {relativeDate(project.created_at)}
            </span>
            <span className="flex items-center gap-1 text-pass font-medium">
              <FileText size={12} />
              {project.document_count ?? 0} docs
            </span>
            <span className="flex items-center gap-1 text-accent font-medium">
              <Layers size={12} />
              {project.blueprint_count ?? blueprints.length} plans
            </span>
          </div>

          <button
            onClick={() => setExpanded(!expanded)}
            className="text-[11px] text-accent hover:underline flex items-center gap-1"
          >
            <span>{expanded ? "Hide Details" : "View Assets"}</span>
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
        </div>

        {/* Expandable Section: Blueprints & Audit Records */}
        {expanded && (
          <div className="mb-4 pt-1 pb-3 text-xs border-b border-edge/60 space-y-3">
            {/* Inner Tabs */}
            <div className="flex items-center gap-1 p-0.5 bg-surface-2 rounded-md border border-edge/60">
              <button
                onClick={() => setTab("blueprints")}
                className={`flex-1 py-1 rounded text-[11px] font-medium transition-colors ${
                  tab === "blueprints" ? "bg-surface-0 text-accent shadow-sm" : "text-ink-dim hover:text-ink"
                }`}
              >
                Blueprints ({blueprints.length})
              </button>
              <button
                onClick={() => setTab("audit")}
                className={`flex-1 py-1 rounded text-[11px] font-medium transition-colors ${
                  tab === "audit" ? "bg-surface-0 text-pass shadow-sm" : "text-ink-dim hover:text-ink"
                }`}
              >
                Audits & Documents ({documents.length})
              </button>
            </div>

            {loadingDetails ? (
              <p className="text-center text-[11px] text-ink-dim py-3">Loading project records...</p>
            ) : tab === "blueprints" ? (
              <div className="space-y-1.5 max-h-44 overflow-y-auto pr-1">
                {blueprints.length === 0 ? (
                  <div className="text-center py-3 text-ink-dim">
                    <p className="text-[11px]">No blueprints generated yet.</p>
                    <Link
                      href={`/editor/${project.id}`}
                      className="text-accent text-[11px] hover:underline mt-1 inline-block"
                    >
                      + Generate first blueprint
                    </Link>
                  </div>
                ) : (
                  blueprints.map((bp) => (
                    <div
                      key={bp.id}
                      className="flex items-center justify-between p-2 rounded-md bg-surface-2/60 border border-edge/40 text-[11px]"
                    >
                      <div>
                        <div className="flex items-center gap-1.5 font-medium text-ink">
                          <Layers size={11} className="text-accent" />
                          <span>Plan {bp.id.slice(0, 8)}</span>
                        </div>
                        <p className="text-[10px] text-ink-dim">
                          {bp.total_area_sqm ? `${bp.total_area_sqm.toFixed(1)} m²` : "Layout"} · {relativeDate(bp.created_at)}
                        </p>
                      </div>

                      <div className="flex items-center gap-1.5">
                        <Link
                          href={`/editor/${project.id}?blueprintId=${bp.id}`}
                          className="px-2 py-0.5 rounded bg-accent/15 text-accent hover:bg-accent/25 font-medium transition-colors"
                        >
                          Open
                        </Link>
                        <button
                          onClick={() => handleDeleteBlueprint(bp.id)}
                          className="text-ink-dim hover:text-fail p-1"
                          title="Delete blueprint"
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            ) : (
              <div className="space-y-2 max-h-44 overflow-y-auto pr-1">
                {/* Documents list */}
                <div>
                  <h4 className="text-[10px] uppercase font-mono text-ink-dim mb-1">Uploaded Documents</h4>
                  {documents.length === 0 ? (
                    <p className="text-[11px] text-ink-dim italic">No documents uploaded.</p>
                  ) : (
                    documents.map((doc: any) => (
                      <div
                        key={doc.id}
                        className="flex items-center justify-between p-1.5 rounded bg-surface-2/50 text-[11px] mb-1"
                      >
                        <span className="flex items-center gap-1.5 truncate text-ink">
                          {doc.kind === "codebook" ? (
                            <FileCode size={12} className="text-review shrink-0" />
                          ) : (
                            <FileText size={12} className="text-pass shrink-0" />
                          )}
                          <span className="truncate">{doc.filename}</span>
                        </span>
                        <span className="text-[10px] text-ink-dim uppercase shrink-0 font-mono">
                          {doc.kind}
                        </span>
                      </div>
                    ))
                  )}
                </div>

                {/* Audit runs list */}
                <div className="pt-1">
                  <h4 className="text-[10px] uppercase font-mono text-ink-dim mb-1">Audit Verification Runs</h4>
                  {runs.length === 0 ? (
                    <p className="text-[11px] text-ink-dim italic">No audits executed yet.</p>
                  ) : (
                    runs.map((run: any) => (
                      <div
                        key={run.run_id}
                        className="flex items-center justify-between p-1.5 rounded bg-surface-2/50 text-[11px] mb-1"
                      >
                        <div>
                          <span className="font-mono text-ink text-[10px]">{run.run_id}</span>
                          <p className="text-[10px] text-ink-dim">
                            {run.complies_count ?? 0} Pass · {run.violates_count ?? 0} Violate · {run.review_count ?? 0} Review
                          </p>
                        </div>
                        <Link
                          href={`/project/${project.id}?run_id=${run.run_id}`}
                          className="px-2 py-0.5 rounded bg-pass/15 text-pass hover:bg-pass/25 font-medium transition-colors text-[10px]"
                        >
                          Results
                        </Link>
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Direct Workspace Actions */}
      <div className="pt-3 border-t border-edge/60 flex items-center gap-2">
        <Link
          href={`/project/${project.id}`}
          className="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md bg-surface-2 hover:bg-pass/10 hover:text-pass border border-edge text-xs font-medium text-ink transition-colors"
        >
          <FileCheck2 size={13} className="text-pass" />
          Compliance Audit
        </Link>
        <Link
          href={`/editor/${project.id}`}
          className="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md bg-surface-2 hover:bg-accent/10 hover:text-accent border border-edge text-xs font-medium text-ink transition-colors"
        >
          <Layers size={13} className="text-accent" />
          Blueprint Studio
        </Link>
      </div>
    </Card>
  );
}

export default function ProjectsPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-surface-0 flex items-center justify-center">
          <Loader2 size={24} className="animate-spin text-accent" />
        </div>
      }
    >
      <ProjectsContent />
    </Suspense>
  );
}
