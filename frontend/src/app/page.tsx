"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  FileCheck2,
  Layers,
  ArrowRight,
  Upload,
  Plus,
  Sparkles,
  Building,
  Clock,
  Trash2,
  FileText,
  Search,
  ExternalLink,
} from "lucide-react";

import { Navbar } from "@/components/Navbar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { GenerateDialog } from "@/components/editor/GenerateDialog";
import { UploadDialog } from "@/components/editor/UploadDialog";
import ConfirmDialog, { type ConfirmRequest } from "@/components/ConfirmDialog";
import { api } from "@/lib/api";
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

export default function UnifiedDashboard() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [nlPrompt, setNlPrompt] = useState("");

  // Dialog states
  const [generateOpen, setGenerateOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectDesc, setNewProjectDesc] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);

  const refreshProjects = useCallback(() => {
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
    refreshProjects();
  }, [refreshProjects]);

  async function handleCreateProject() {
    if (!newProjectName.trim()) return;
    setBusyAction("creating");
    try {
      const proj = await api.createProject(newProjectName.trim(), newProjectDesc.trim() || undefined);
      toast.success("Project created");
      setCreateProjectOpen(false);
      setNewProjectName("");
      setNewProjectDesc("");
      router.push(`/project/${proj.id}`);
    } catch (e: any) {
      toast.error(e.message || "Failed to create project");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleLoadSample() {
    setBusyAction("sample");
    try {
      const sample = await api.createSampleProject();
      toast.success("Loaded bundled ADA door clearance sample");
      router.push(`/project/${sample.id}`);
    } catch (e: any) {
      toast.error(e.message || "Failed to load sample project");
    } finally {
      setBusyAction(null);
    }
  }

  function handleQuickGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!nlPrompt.trim()) {
      setGenerateOpen(true);
      return;
    }
    router.push(`/editor/new?prompt=${encodeURIComponent(nlPrompt.trim())}`);
  }

  function handleDeleteProject(projectId: string, projectName: string) {
    setConfirm({
      title: `Delete "${projectName}"?`,
      body: "All documents, generated blueprints, and verification audit results in this project will be permanently deleted from the database.",
      confirmLabel: "Delete Project",
      onConfirm: async () => {
        try {
          await api.deleteProject(projectId);
          toast.success("Project deleted");
          refreshProjects();
        } catch (e: any) {
          toast.error(e.message || "Failed to delete project");
        }
      },
    });
  }

  const filteredProjects = projects.filter((p) => {
    if (!search.trim()) return true;
    const term = search.toLowerCase();
    return (
      p.name.toLowerCase().includes(term) ||
      (p.description && p.description.toLowerCase().includes(term))
    );
  });

  return (
    <div className="min-h-screen bg-surface-0 flex flex-col">
      <Navbar />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8 sm:py-12">
        {/* Hero Banner with Required Title */}
        <div className="mb-10 text-center sm:text-left">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent/10 border border-accent/20 text-accent text-xs font-medium mb-3">
            <Sparkles size={14} />
            Unified AEC Intelligence Suite
          </div>
          <h1 className="text-3xl sm:text-4xl font-bold tracking-tight text-ink font-sans">
            What would you like to do today?
          </h1>
          <p className="mt-2 text-sm sm:text-base text-ink-dim max-w-2xl">
            Design compliant floor plans with generative diffusion models, or verify existing drawings against regional building codes in an interactive dual-pane audit environment.
          </p>
        </div>

        {/* Primary Workflow Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-14">
          {/* Card 1: Check Building Compliance (PlanLint) */}
          <Card className="relative overflow-hidden border-edge bg-surface-1/70 backdrop-blur-md p-6 sm:p-7 hover:border-pass/50 hover:shadow-lg hover:shadow-pass/5 transition-all flex flex-col justify-between group">
            <div className="absolute top-0 right-0 w-44 h-44 bg-pass/5 rounded-full blur-3xl pointer-events-none" />
            <div>
              <div className="flex items-center justify-between mb-4">
                <div className="w-12 h-12 rounded-xl bg-pass/10 border border-pass/30 flex items-center justify-center text-pass">
                  <FileCheck2 size={24} />
                </div>
                <span className="text-xs font-mono px-2.5 py-1 rounded-full bg-pass/10 text-pass border border-pass/20 font-medium">
                  Compliance Audit
                </span>
              </div>
              <h2 className="text-xl font-semibold text-ink group-hover:text-pass transition-colors">
                Check Building Compliance
              </h2>
              <p className="mt-2 text-xs sm:text-sm text-ink-dim leading-relaxed">
                Run deterministic and vision-language audits on floor plan sheets against ADA, NBC, and IBC codes. Inspect vector-snapped door clearances, corridor widths, and regulation citations.
              </p>
            </div>

            <div className="mt-6 pt-5 border-t border-edge/60 flex flex-wrap items-center gap-3">
              <Button
                onClick={() => setCreateProjectOpen(true)}
                className="bg-pass hover:bg-pass/90 text-surface-0 font-medium text-xs h-9 px-4"
              >
                Start New Audit
                <ArrowRight size={14} className="ml-1.5" />
              </Button>
              <Button
                variant="outline"
                onClick={handleLoadSample}
                disabled={busyAction === "sample"}
                className="border-edge bg-surface-2 text-ink hover:bg-surface-2/80 text-xs h-9 px-3"
              >
                <Building size={14} className="mr-1.5 text-pass" />
                {busyAction === "sample" ? "Loading Sample..." : "Explore ADA Sample"}
              </Button>
            </div>
          </Card>

          {/* Card 2: Generate a Blueprint (ArchiGen) */}
          <Card className="relative overflow-hidden border-edge bg-surface-1/70 backdrop-blur-md p-6 sm:p-7 hover:border-accent/50 hover:shadow-lg hover:shadow-accent/5 transition-all flex flex-col justify-between group">
            <div className="absolute top-0 right-0 w-44 h-44 bg-accent/5 rounded-full blur-3xl pointer-events-none" />
            <div>
              <div className="flex items-center justify-between mb-4">
                <div className="w-12 h-12 rounded-xl bg-accent/10 border border-accent/30 flex items-center justify-center text-accent">
                  <Layers size={24} />
                </div>
                <span className="text-xs font-mono px-2.5 py-1 rounded-full bg-accent/10 text-accent border border-accent/20 font-medium">
                  Blueprint Studio
                </span>
              </div>
              <h2 className="text-xl font-semibold text-ink group-hover:text-accent transition-colors">
                Generate a Blueprint
              </h2>
              <p className="mt-2 text-xs sm:text-sm text-ink-dim leading-relaxed">
                Generate residential layouts from prompts with HouseDiffusion ML models, edit room geometries in 2D/3D canvas, check municipal zoning limits, and export to DXF and SVG.
              </p>

              {/* Quick Prompt Input */}
              <form onSubmit={handleQuickGenerate} className="mt-4 flex gap-2">
                <Input
                  value={nlPrompt}
                  onChange={(e) => setNlPrompt(e.target.value)}
                  placeholder="e.g. 3-bedroom bungalow with open kitchen on a 15x20m lot..."
                  className="bg-surface-2 border-edge text-ink text-xs h-9 placeholder:text-ink-dim/50 focus-visible:ring-accent"
                />
                <Button
                  type="submit"
                  size="sm"
                  className="bg-accent hover:bg-accent/90 text-white text-xs h-9 px-3 shrink-0"
                >
                  <Sparkles size={14} className="mr-1" />
                  Generate
                </Button>
              </form>
            </div>

            <div className="mt-6 pt-5 border-t border-edge/60 flex flex-wrap items-center gap-3">
              <Button
                variant="outline"
                onClick={() => setGenerateOpen(true)}
                className="border-edge bg-surface-2 text-ink hover:bg-surface-2/80 text-xs h-9 px-3"
              >
                <Plus size={14} className="mr-1.5 text-accent" />
                Advanced Spec Builder
              </Button>
              <Button
                variant="outline"
                onClick={() => setUploadOpen(true)}
                className="border-edge bg-surface-2 text-ink hover:bg-surface-2/80 text-xs h-9 px-3"
              >
                <Upload size={14} className="mr-1.5 text-ink-dim" />
                Import Plan (PDF, DXF, IFC)
              </Button>
            </div>
          </Card>
        </div>

        {/* Unified Projects Section */}
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-ink flex items-center gap-2">
                Unified Projects
                <span className="text-xs px-2 py-0.5 rounded-full bg-surface-2 text-ink-dim border border-edge">
                  {projects.length}
                </span>
              </h2>
              <p className="text-xs text-ink-dim">
                Persistent projects containing compliance audit runs, uploaded documents, and generated blueprints.
              </p>
            </div>

            <div className="flex items-center gap-2">
              <div className="relative w-48 sm:w-64">
                <Search size={14} className="absolute left-3 top-2.5 text-ink-dim" />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Filter projects..."
                  className="bg-surface-1 border-edge text-ink pl-8 text-xs h-8 placeholder:text-ink-dim/50"
                />
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setCreateProjectOpen(true)}
                className="border-edge bg-surface-1 text-ink hover:bg-surface-2 text-xs h-8"
              >
                <Plus size={14} className="mr-1" />
                New Project
              </Button>
            </div>
          </div>

          {/* Project Cards Grid */}
          {loading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-40 rounded-xl bg-surface-1/40 border border-edge animate-pulse" />
              ))}
            </div>
          ) : filteredProjects.length === 0 ? (
            <div className="border border-dashed border-edge rounded-xl p-10 text-center bg-surface-1/20">
              <Building size={32} className="mx-auto text-ink-dim/40 mb-3" />
              <p className="text-sm text-ink font-medium">No projects found</p>
              <p className="text-xs text-ink-dim max-w-sm mx-auto mt-1 mb-4">
                Create a project to store compliance audit runs and generated floor plans.
              </p>
              <Button
                size="sm"
                onClick={() => setCreateProjectOpen(true)}
                className="bg-accent hover:bg-accent/90 text-white text-xs"
              >
                Create Your First Project
              </Button>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {filteredProjects.map((project) => (
                <Card
                  key={project.id}
                  className="border-edge bg-surface-1 hover:border-edge/90 transition-all flex flex-col justify-between p-4 group"
                >
                  <div>
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <h3 className="font-medium text-sm text-ink truncate group-hover:text-accent transition-colors">
                        {project.name}
                      </h3>
                      <button
                        onClick={() => handleDeleteProject(project.id, project.name)}
                        className="text-ink-dim hover:text-fail p-1 rounded transition-colors opacity-60 hover:opacity-100"
                        title="Delete project"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>

                    {project.description && (
                      <p className="text-xs text-ink-dim line-clamp-2 mb-3">
                        {project.description}
                      </p>
                    )}

                    <div className="flex items-center gap-3 text-[11px] text-ink-dim mb-4">
                      <span className="flex items-center gap-1">
                        <Clock size={12} />
                        {relativeDate(project.created_at)}
                      </span>
                      <span className="flex items-center gap-1">
                        <FileText size={12} className="text-pass" />
                        {project.document_count ?? 0} docs
                      </span>
                      <span className="flex items-center gap-1">
                        <Layers size={12} className="text-accent" />
                        {project.blueprint_count ?? 0} plans
                      </span>
                    </div>
                  </div>

                  {/* Dual Action Links */}
                  <div className="pt-3 border-t border-edge/60 flex items-center gap-2">
                    <Link
                      href={`/project/${project.id}`}
                      className="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md bg-surface-2 hover:bg-pass/10 hover:text-pass border border-edge text-xs font-medium text-ink transition-colors"
                      title="Open compliance audit workspace"
                    >
                      <FileCheck2 size={13} className="text-pass" />
                      Compliance
                    </Link>
                    <Link
                      href={`/editor/${project.id}`}
                      className="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md bg-surface-2 hover:bg-accent/10 hover:text-accent border border-edge text-xs font-medium text-ink transition-colors"
                      title="Open blueprint editor"
                    >
                      <Layers size={13} className="text-accent" />
                      Studio
                    </Link>
                  </div>
                </Card>
              ))}
            </div>
          )}
        </div>
      </main>

      {/* Dialogs */}
      <GenerateDialog open={generateOpen} onOpenChange={setGenerateOpen} />
      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploadSuccess={(generationId, targetProjectId) => {
          setUploadOpen(false);
          router.push(`/editor/${targetProjectId}?blueprintId=${generationId}`);
        }}
      />

      {/* Create Project Modal */}
      {createProjectOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-surface-1 border border-edge rounded-xl max-w-md w-full p-6 shadow-2xl">
            <h3 className="text-base font-semibold text-ink mb-1">Create Project</h3>
            <p className="text-xs text-ink-dim mb-4">
              Create an architectural workspace for code compliance verification and blueprint designs.
            </p>
            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-ink mb-1 block">Project Name</label>
                <Input
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  placeholder="e.g. West Coast Medical Center"
                  className="bg-surface-2 border-edge text-ink text-xs"
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs font-medium text-ink mb-1 block">Description (Optional)</label>
                <Input
                  value={newProjectDesc}
                  onChange={(e) => setNewProjectDesc(e.target.value)}
                  placeholder="e.g. ADA accessibility audit and floor plan design"
                  className="bg-surface-2 border-edge text-ink text-xs"
                />
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCreateProjectOpen(false)}
                className="border-edge bg-surface-2 text-ink hover:bg-surface-2/80 text-xs"
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={handleCreateProject}
                disabled={!newProjectName.trim() || busyAction === "creating"}
                className="bg-accent hover:bg-accent/90 text-white text-xs"
              >
                {busyAction === "creating" ? "Creating..." : "Create Project"}
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
