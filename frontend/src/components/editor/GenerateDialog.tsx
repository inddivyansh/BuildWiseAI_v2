"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Loader2,
  Sparkles,
  Zap,
  Bot,
  Scale,
  Ruler,
  FolderKanban,
  CheckCircle2,
} from "lucide-react";
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
import { toast } from "sonner";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId?: string;
}

const SAMPLE_PROMPTS = [
  {
    title: "🏡 3-Bed Family Bungalow",
    desc: "15×20m lot • 2 baths • Open kitchen • Garage",
    prompt:
      "A modern 3-bedroom bungalow on a 15x20m lot with an open-concept kitchen, dining area, 2 bathrooms, laundry room, and a single-car garage.",
    width: "15",
    depth: "20",
  },
  {
    title: "🏢 4-Bed 2-Story Residence",
    desc: "18×25m lot • Master suite • Office • Double garage",
    prompt:
      "Modern 4-bedroom 2-story family home with a master bedroom suite, 3 secondary bedrooms, 2.5 bathrooms, home office, open kitchen, and a double garage on an 18x25m lot.",
    width: "18",
    depth: "25",
  },
  {
    title: "🛋️ Compact 2-Bed Suite",
    desc: "12×16m lot • Open living • 1 bath • Patio",
    prompt:
      "Compact 2-bedroom single-story home with an open-plan kitchen and living room, 1 bathroom, and outdoor patio on a 12x16m lot.",
    width: "12",
    depth: "16",
  },
];

export function GenerateDialog({ open, onOpenChange, projectId }: Props) {
  const router = useRouter();
  const { setPlan, setCompliance, setGenerating } = useFloorPlanStore();

  const [prompt, setPrompt] = useState("");
  const [plotWidth, setPlotWidth] = useState("");
  const [plotDepth, setPlotDepth] = useState("");
  const [zoneId, setZoneId] = useState("");
  const [generator, setGenerator] = useState("housediffusion");
  const [runCompliance, setRunCompliance] = useState(true);

  // Generation & Compliance modes (Rule-based vs LLM)
  const [generationMode, setGenerationMode] = useState<"rules" | "llm">("rules");
  const [complianceMode, setComplianceMode] = useState<"rules" | "llm">("rules");

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
      toast.success(
        generationMode === "rules"
          ? "Floor plan generated via deterministic rules (0s, offline)"
          : "Floor plan generated and saved to project"
      );
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

  const applySamplePrompt = (sample: (typeof SAMPLE_PROMPTS)[number]) => {
    setPrompt(sample.prompt);
    setPlotWidth(sample.width);
    setPlotDepth(sample.depth);
    toast.info(`Applied sample: ${sample.title}`);
  };

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
        generation_mode: generationMode,
        compliance_mode: complianceMode,
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
      <DialogContent className="sm:max-w-xl max-h-[92vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles size={18} className="text-accent" />
            New Floor Plan Studio
          </DialogTitle>
          <DialogDescription>
            Generate production-ready architectural layouts using deterministic rule heuristics or natural language AI models.
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

          {/* Sample Prompts */}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                <Sparkles size={13} className="text-accent" />
                Sample Prompts
              </Label>
              <span className="text-[10px] text-muted-foreground">Click template to auto-fill</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {SAMPLE_PROMPTS.map((sample, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => applySamplePrompt(sample)}
                  className="text-left p-2.5 rounded-lg border border-border/70 hover:border-accent hover:bg-accent/5 bg-muted/20 transition-all group"
                >
                  <div className="text-xs font-semibold text-foreground group-hover:text-accent transition-colors flex items-center justify-between">
                    <span>{sample.title}</span>
                  </div>
                  <div className="text-[10px] text-muted-foreground mt-1 line-clamp-2">
                    {sample.desc}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Prompt Field */}
          <div className="flex flex-col gap-2">
            <Label htmlFor="prompt">Floor Plan Description</Label>
            <div className="relative">
              <Textarea
                id="prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="A modern 3-bedroom bungalow with an open-concept kitchen and living area, 2 bathrooms, and a single-car garage..."
                className="min-h-[105px] resize-none pr-16 text-xs leading-relaxed"
                maxLength={5000}
              />
              <span className="absolute bottom-2 right-3 text-[10px] text-muted-foreground">
                {prompt.length}/5000
              </span>
            </div>
          </div>

          {/* Generation Engine (Rules vs LLM) */}
          <div className="p-3 rounded-lg border border-border/70 bg-muted/20 space-y-2.5">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                <Zap size={13} className="text-amber-400" />
                Generation Engine (Layout Parsing)
              </Label>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-accent/10 text-accent font-medium">
                {generationMode === "rules" ? "Deterministic (0 API Calls)" : "AI Natural Language"}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button
                type="button"
                variant={generationMode === "rules" ? "default" : "outline"}
                size="sm"
                className={`text-xs h-9 justify-center transition-all ${
                  generationMode === "rules"
                    ? "bg-accent hover:bg-accent/90 text-white font-medium shadow-sm"
                    : "border-border text-foreground hover:bg-muted/40"
                }`}
                onClick={() => setGenerationMode("rules")}
              >
                <Zap size={14} className="mr-1.5 text-amber-300" />
                Rule-Based (Instant / No LLM)
              </Button>
              <Button
                type="button"
                variant={generationMode === "llm" ? "default" : "outline"}
                size="sm"
                className={`text-xs h-9 justify-center transition-all ${
                  generationMode === "llm"
                    ? "bg-accent hover:bg-accent/90 text-white font-medium shadow-sm"
                    : "border-border text-foreground hover:bg-muted/40"
                }`}
                onClick={() => setGenerationMode("llm")}
              >
                <Bot size={14} className="mr-1.5" />
                AI Model (LLM)
              </Button>
            </div>
            <p className="text-[10px] text-muted-foreground flex items-center gap-1.5">
              <CheckCircle2 size={12} className={generationMode === "rules" ? "text-emerald-400" : "text-accent"} />
              {generationMode === "rules"
                ? "Instant ~2ms parsing via architectural heuristics. Zero LLM API calls, zero quota limits."
                : "Deep natural language parsing with automatic multi-model failover (Gemini / Ollama)."}
            </p>
          </div>

          {/* Compliance Engine (Rules vs LLM) */}
          <div className="p-3 rounded-lg border border-border/70 bg-muted/20 space-y-2.5">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-medium text-foreground flex items-center gap-1.5">
                <Scale size={13} className="text-emerald-400" />
                Compliance Engine (Code Verification)
              </Label>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 font-medium">
                {complianceMode === "rules" ? "Deterministic Rules" : "AI Code Reasoning"}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button
                type="button"
                variant={complianceMode === "rules" ? "default" : "outline"}
                size="sm"
                className={`text-xs h-9 justify-center transition-all ${
                  complianceMode === "rules"
                    ? "bg-emerald-600 hover:bg-emerald-500 text-white font-medium shadow-sm"
                    : "border-border text-foreground hover:bg-muted/40"
                }`}
                onClick={() => setComplianceMode("rules")}
              >
                <Ruler size={14} className="mr-1.5 text-emerald-200" />
                Deterministic Rules (No LLM)
              </Button>
              <Button
                type="button"
                variant={complianceMode === "llm" ? "default" : "outline"}
                size="sm"
                className={`text-xs h-9 justify-center transition-all ${
                  complianceMode === "llm"
                    ? "bg-emerald-600 hover:bg-emerald-500 text-white font-medium shadow-sm"
                    : "border-border text-foreground hover:bg-muted/40"
                }`}
                onClick={() => setComplianceMode("llm")}
              >
                <Bot size={14} className="mr-1.5" />
                AI Model (LLM Reasoning)
              </Button>
            </div>
            <p className="text-[10px] text-muted-foreground flex items-center gap-1.5">
              <CheckCircle2 size={12} className={complianceMode === "rules" ? "text-emerald-400" : "text-emerald-300"} />
              {complianceMode === "rules"
                ? "Evaluates NBC Part 9 dimensional rules, setbacks, and lot coverage mathematically without LLM calls."
                : "Combines deterministic checks with Tier 3 LLM legal reasoning over municipal zoning articles."}
            </p>
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
                  className="h-8 text-xs"
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
                  className="h-8 text-xs"
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
                  m
                </span>
              </div>
            </div>
          </div>

          {/* Zone selector */}
          <div className="flex flex-col gap-1.5">
            <Label>Municipal Zone (Optional)</Label>
            <Select value={zoneId} onValueChange={(v) => setZoneId(v ?? "")}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="Select a zone (optional)" />
              </SelectTrigger>
              <SelectContent>
                {zonesData?.zones.map((z) => (
                  <SelectItem key={z} value={z} className="text-xs">
                    {z}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* AI Model (Shown with status badge depending on mode) */}
          <div
            className={`flex flex-col gap-1.5 p-3 rounded-lg border border-border/60 transition-opacity ${
              generationMode === "rules" && complianceMode === "rules"
                ? "bg-muted/10 opacity-70"
                : "bg-muted/20"
            }`}
          >
            <div className="flex items-center justify-between">
              <Label className="text-xs font-medium text-foreground">AI Model (Fallback / Free Tier)</Label>
              {generationMode === "rules" && complianceMode === "rules" ? (
                <span className="text-[10px] text-muted-foreground font-medium">Bypassed (No LLM Active)</span>
              ) : (
                <span className="text-[10px] text-emerald-400 font-medium">Active for LLM Mode</span>
              )}
            </div>
            <ModelSelector className="w-full" />
            <p className="text-[10px] text-muted-foreground">
              {generationMode === "rules" && complianceMode === "rules"
                ? "Both generation and compliance are running 100% locally with zero external API calls."
                : "Select among supported free-tier and local AI models with automatic failover."}
            </p>
          </div>

          {/* Generator + Compliance Toggle row */}
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Layout Generator</Label>
              <Select value={generator} onValueChange={(v) => setGenerator(v ?? "housediffusion")}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="housediffusion" className="text-xs">
                    HouseDiffusion
                  </SelectItem>
                  <SelectItem value="gsdiff" className="text-xs">
                    GSDiff
                  </SelectItem>
                  <SelectItem value="chathousediffusion" className="text-xs">
                    ChatHouseDiffusion
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Audit Status</Label>
              <Button
                type="button"
                variant={runCompliance ? "default" : "outline"}
                size="sm"
                className="w-full justify-start h-8 text-xs"
                onClick={() => setRunCompliance(!runCompliance)}
              >
                {runCompliance ? "✓ Compliance Audit Active" : "✕ Audit Disabled"}
              </Button>
            </div>
          </div>

          {/* Generate button */}
          <Button
            onClick={handleGenerate}
            disabled={generateMutation.isPending || prompt.length < 10}
            className="w-full mt-2 h-10 bg-accent hover:bg-accent/90 text-white font-medium shadow-md transition-all"
          >
            {generateMutation.isPending ? (
              <>
                <Loader2 size={16} className="mr-2 animate-spin" />
                Generating Blueprint...
              </>
            ) : (
              <>
                <Sparkles size={16} className="mr-2" />
                Generate Floor Plan ({generationMode === "rules" ? "Rule-Based" : "AI LLM"})
              </>
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
