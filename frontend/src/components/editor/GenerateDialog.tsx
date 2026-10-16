"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { generateFloorPlan, listZones, listProjects, api, type ProjectSummary } from "@/lib/api";
import { useFloorPlanStore } from "@/stores/floorPlanStore";
import { useModelStore } from "@/stores/modelStore";
import { ModelSelector } from "@/components/editor/ModelSelector";
import { FolderKanban } from "lucide-react";
import { toast } from "sonner";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId?: string;
}

export function GenerateDialog({ open, onOpenChange, projectId }: Props) {
  const router = useRouter();
  const { setPlan, setCompliance, setGenerating } = useFloorPlanStore();

  const [prompt, setPrompt] = useState("");
  const [plotWidth, setPlotWidth] = useState("");
  const [plotDepth, setPlotDepth] = useState("");
  const [zoneId, setZoneId] = useState("");
  const [generator, setGenerator] = useState("housediffusion");
  const [runCompliance, setRunCompliance] = useState(true);

  // Project selection state when not pre-scoped to a project
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("new");
  const [newProjectName, setNewProjectName] = useState<string>("");
  const [activeTargetProjectId, setActiveTargetProjectId] = useState<string | undefined>(projectId);

  useEffect(() => {
    if (open && !projectId) {
      listProjects()
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

  const { data: zonesData } = useQuery({
    queryKey: ["zones"],
    queryFn: listZones,
    enabled: open,
  });

  const generateMutation = useMutation({
    mutationFn: generateFloorPlan,
    onMutate: () => {
      setGenerating(true);
    },
    onSuccess: (data) => {
      if (data.floor_plan) {
        setPlan(data.floor_plan);
      }
      if (data.compliance) {
        setCompliance(data.compliance);
      }
      toast.success("Floor plan generated and saved to project");
      onOpenChange(false);
      const bpId = data.floor_plan?.generation_id || data.generation_id;
      const targetId = activeTargetProjectId || projectId || data.project_id || "new";
      router.push(`/editor/${targetId}?blueprintId=${bpId}`);
    },
    onError: (err) => {
      setGenerating(false);
      toast.error(err instanceof Error ? err.message : "Generation failed");
    },
  });

  const handleGenerate = async () => {
    if (prompt.length < 10) {
      toast.error("Prompt must be at least 10 characters");
      return;
    }

    let targetProjectId = projectId;
    try {
      if (!targetProjectId) {
        if (selectedProjectId === "new" || !selectedProjectId) {
          const title = newProjectName.trim() || prompt.slice(0, 32) || "New Blueprint Project";
          const newProj = await api.createProject(title, prompt.slice(0, 80));
          targetProjectId = newProj.id;
        } else {
          targetProjectId = selectedProjectId;
        }
      }
      setActiveTargetProjectId(targetProjectId);

      const { selectedModelId, getSelectedModel } = useModelStore.getState();
      const activeModel = getSelectedModel();

      generateMutation.mutate({
        prompt,
        project_id: targetProjectId,
        plot_width_m: plotWidth ? parseFloat(plotWidth) : undefined,
        plot_depth_m: plotDepth ? parseFloat(plotDepth) : undefined,
        zone_id: zoneId || undefined,
        generator,
        run_compliance: runCompliance,
        model: selectedModelId,
        provider: activeModel?.provider,
      });
    } catch (err: any) {
      setGenerating(false);
      toast.error(err.message || "Failed to initialize project");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New Floor Plan</DialogTitle>
          <DialogDescription>
            Describe the floor plan you want to generate.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 mt-2">
          {/* Target Project (if not scoped to a specific project) */}
          {!projectId && (
            <div className="space-y-2 p-3 bg-muted/40 rounded-lg border border-border/60">
              <Label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                <FolderKanban size={13} className="text-accent" />
                Target Project
              </Label>
              <Select
                value={selectedProjectId}
                onValueChange={(val) => setSelectedProjectId(val || "new")}
              >
                <SelectTrigger className="bg-background border-border text-foreground text-xs h-8">
                  <SelectValue placeholder="Select destination project" />
                </SelectTrigger>
                <SelectContent>
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
                <div className="pt-1">
                  <Input
                    placeholder="Project name (e.g. Modern Villa)..."
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    className="bg-background border-border text-foreground text-xs h-8"
                  />
                </div>
              )}
            </div>
          )}

          {/* Prompt */}
          <div className="flex flex-col gap-2">
            <Label htmlFor="prompt">Description</Label>
            <div className="relative">
              <Textarea
                id="prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="A modern 3-bedroom bungalow with an open-concept kitchen and living area, 2 bathrooms, and a single-car garage..."
                className="min-h-[120px] resize-none pr-16"
                maxLength={5000}
              />
              <span className="absolute bottom-2 right-3 text-xs text-muted-foreground">
                {prompt.length}/5000
              </span>
            </div>
          </div>

          {/* Plot dimensions */}
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="width">Plot Width</Label>
              <div className="relative">
                <Input
                  id="width"
                  type="number"
                  step="0.5"
                  min="5"
                  max="100"
                  placeholder="e.g. 15"
                  value={plotWidth}
                  onChange={(e) => setPlotWidth(e.target.value)}
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
                  m
                </span>
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="depth">Plot Depth</Label>
              <div className="relative">
                <Input
                  id="depth"
                  type="number"
                  step="0.5"
                  min="5"
                  max="100"
                  placeholder="e.g. 20"
                  value={plotDepth}
                  onChange={(e) => setPlotDepth(e.target.value)}
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
                  m
                </span>
              </div>
            </div>
          </div>

          {/* Zone selector */}
          <div className="flex flex-col gap-1.5">
            <Label>Zone</Label>
            <Select value={zoneId} onValueChange={(v) => setZoneId(v ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Select a zone (optional)" />
              </SelectTrigger>
              <SelectContent>
                {zonesData?.zones.map((z) => (
                  <SelectItem key={z} value={z}>
                    {z}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* AI Model (Free-Tier Prioritized) */}
          <div className="flex flex-col gap-1.5 p-3 rounded-lg border border-border/60 bg-muted/20">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-medium text-foreground">AI Model (Free Tier)</Label>
              <span className="text-[10px] text-emerald-400 font-medium">Free Tier Active</span>
            </div>
            <ModelSelector className="w-full" />
            <p className="text-[10px] text-muted-foreground">
              Select among supported free-tier and local AI models for architectural layout extraction.
            </p>
          </div>

          {/* Generator + Compliance row */}
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Generator</Label>
              <Select value={generator} onValueChange={(v) => setGenerator(v ?? "housediffusion")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="housediffusion">
                    HouseDiffusion
                  </SelectItem>
                  <SelectItem value="gsdiff">GSDiff</SelectItem>
                  <SelectItem value="chathousediffusion">
                    ChatHouseDiffusion
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Compliance</Label>
              <Button
                type="button"
                variant={runCompliance ? "default" : "outline"}
                className="w-full justify-start"
                onClick={() => setRunCompliance(!runCompliance)}
              >
                {runCompliance ? "Enabled" : "Disabled"}
              </Button>
            </div>
          </div>

          {/* Generate button */}
          <Button
            onClick={handleGenerate}
            disabled={generateMutation.isPending || prompt.length < 10}
            className="w-full mt-1"
          >
            {generateMutation.isPending ? (
              <>
                <Loader2 size={16} className="mr-2 animate-spin" />
                Generating...
              </>
            ) : (
              <>
                <Sparkles size={16} className="mr-2" />
                Generate Floor Plan
              </>
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
