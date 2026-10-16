"use client";

import React, { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Sparkles,
  ChevronDown,
  Check,
  Lock,
  AlertTriangle,
  Zap,
  HardDrive,
  Cpu,
  Info,
  ExternalLink,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { getAvailableModels } from "@/lib/api";
import { useModelStore } from "@/stores/modelStore";
import type { AIModel } from "@/types/model";
import { toast } from "sonner";

interface ModelSelectorProps {
  compact?: boolean;
  className?: string;
  onModelChange?: (model: AIModel) => void;
}

export function ModelSelector({
  compact = false,
  className = "",
  onModelChange,
}: ModelSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const { selectedModelId, setSelectedModelId, setModelsData } = useModelStore();

  const { data: modelsData, isLoading } = useQuery({
    queryKey: ["ai_models"],
    queryFn: getAvailableModels,
    staleTime: 60_000,
  });

  useEffect(() => {
    if (modelsData) {
      setModelsData(modelsData);
    }
  }, [modelsData, setModelsData]);

  // Close dropdown on click outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  const allModels = modelsData?.models || [];
  const selectedModel = allModels.find((m) => m.id === selectedModelId) || allModels[0];

  const freeModels = allModels.filter((m) => m.tier === "free");
  const premiumModels = allModels.filter((m) => m.tier === "premium");

  const handleSelect = (model: AIModel) => {
    if (!model.is_selectable) {
      toast.info(`'${model.name}' is coming soon in the upcoming commercial release.`);
      return;
    }

    if (!model.is_available && model.requires_key) {
      toast.warning(
        `Please configure ${model.env_key_name} in your .env file to enable ${model.name}.`,
        {
          description: "Free-tier API keys can be obtained with zero billing from the provider.",
          duration: 6000,
        }
      );
    }

    setSelectedModelId(model.id);
    onModelChange?.(model);
    setIsOpen(false);
    toast.success(`Active AI Model: ${model.name}`);
  };

  const getProviderIcon = (provider: string) => {
    switch (provider) {
      case "gemini":
        return <Sparkles className="size-3.5 text-blue-400" />;
      case "groq":
        return <Zap className="size-3.5 text-orange-400" />;
      case "xai":
        return <Cpu className="size-3.5 text-purple-400" />;
      case "ollama":
        return <HardDrive className="size-3.5 text-emerald-400" />;
      default:
        return <Sparkles className="size-3.5 text-accent" />;
    }
  };

  return (
    <div className={`relative inline-block ${className}`} ref={containerRef}>
      {/* Trigger Button */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        disabled={isLoading}
        className={`flex items-center gap-1.5 transition-all text-xs font-medium rounded-lg border outline-none select-none ${
          compact
            ? "px-2 py-1 bg-surface-1/80 hover:bg-surface-2 border-edge text-ink hover:text-ink-bright"
            : "px-3 py-1.5 bg-surface-1 hover:bg-surface-2 border-edge text-ink shadow-sm"
        }`}
        title="Select AI Model (Free-Tier & Local Models)"
      >
        <span className="flex items-center gap-1.5 truncate max-w-[200px]">
          {selectedModel ? (
            <>
              {getProviderIcon(selectedModel.provider)}
              <span className="truncate">{selectedModel.name}</span>
            </>
          ) : (
            <>
              <Sparkles className="size-3 text-accent" />
              <span>Select Model</span>
            </>
          )}
        </span>

        {selectedModel && (
          <span
            className={`inline-block size-1.5 rounded-full shrink-0 ${
              selectedModel.is_available ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)]" : "bg-amber-500"
            }`}
            title={selectedModel.is_available ? "Ready & Available" : `Needs ${selectedModel.env_key_name}`}
          />
        )}

        <ChevronDown className="size-3 text-ink-dim shrink-0 opacity-70" />
      </button>

      {/* Popover / Dropdown Menu */}
      {isOpen && (
        <div className="absolute bottom-full mb-1.5 left-0 z-50 w-84 sm:w-96 rounded-xl border border-edge bg-surface-1 p-2 text-ink shadow-2xl backdrop-blur-xl animate-in fade-in-0 zoom-in-95">
          {/* Header */}
          <div className="px-2.5 py-1.5 border-b border-edge/60 mb-1.5 flex items-center justify-between">
            <div>
              <div className="text-[11px] font-semibold text-ink-bright uppercase tracking-wider flex items-center gap-1.5">
                <Sparkles className="size-3 text-accent" />
                AI Model Engine
              </div>
              <div className="text-[10px] text-ink-dim">
                Prioritizing free-tier APIs and local inference
              </div>
            </div>
            <span className="text-[10px] text-pass font-medium bg-pass/10 px-1.5 py-0.5 rounded border border-pass/20">
              Free Tiers Active
            </span>
          </div>

          <div className="max-h-[360px] overflow-y-auto space-y-3 pr-1 text-xs">
            {/* Group 1: Free Tier Models */}
            <div>
              <div className="px-2.5 py-1 text-[10px] font-semibold text-ink-dim uppercase tracking-wider flex items-center justify-between">
                <span>Free-Tier & Local Models</span>
                <span className="text-ink-muted text-[9px]">Selectable</span>
              </div>

              <div className="space-y-1 mt-0.5">
                {freeModels.map((model) => {
                  const isSelected = model.id === selectedModelId;
                  return (
                    <button
                      key={model.id}
                      type="button"
                      onClick={() => handleSelect(model)}
                      className={`w-full text-left p-2 rounded-lg transition-all flex flex-col gap-1 border ${
                        isSelected
                          ? "bg-accent/15 border-accent/40 text-ink-bright shadow-sm"
                          : "hover:bg-surface-2/60 border-transparent text-ink"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-1.5">
                        <div className="flex items-center gap-1.5 font-medium">
                          {getProviderIcon(model.provider)}
                          <span className="font-semibold text-ink-bright">{model.name}</span>
                          <span
                            className={`inline-block size-1.5 rounded-full ${
                              model.is_available ? "bg-emerald-500" : "bg-amber-500"
                            }`}
                            title={model.is_available ? "Available" : "Unconfigured key"}
                          />
                        </div>

                        <div className="flex items-center gap-1 shrink-0">
                          <span className="text-[10px] px-1.5 py-0.2 rounded-full font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/25">
                            {model.badge}
                          </span>
                          {isSelected && <Check className="size-3 text-accent ml-1" />}
                        </div>
                      </div>

                      <div className="text-[11px] text-ink-dim leading-snug line-clamp-1">
                        {model.capabilities}
                      </div>

                      {!model.is_available && model.requires_key && (
                        <div className="text-[10px] text-amber-400/90 flex items-center gap-1 mt-0.5 bg-amber-500/10 px-1.5 py-0.5 rounded">
                          <AlertTriangle className="size-2.5 shrink-0" />
                          <span>Requires {model.env_key_name} in .env</span>
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Group 2: Premium Models — Coming Soon */}
            <div className="border-t border-edge/60 pt-2">
              <div className="px-2.5 py-1 text-[10px] font-semibold text-ink-dim uppercase tracking-wider flex items-center justify-between">
                <span>Premium Models</span>
                <span className="text-amber-400 text-[9px] font-medium">Coming Soon</span>
              </div>

              <div className="space-y-1 mt-0.5">
                {premiumModels.map((model) => (
                  <div
                    key={model.id}
                    className="p-2 rounded-lg border border-edge/30 bg-surface-0/40 text-ink-muted flex flex-col gap-0.5 cursor-not-allowed opacity-60"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5 font-medium">
                        <Lock className="size-3 text-ink-dim" />
                        <span>{model.name}</span>
                        <span className="text-[10px] text-ink-dim/80">({model.provider_name})</span>
                      </div>
                      <Badge variant="outline" className="text-[9px] px-1.5 py-0 border-amber-500/30 text-amber-400/90">
                        Coming Soon
                      </Badge>
                    </div>
                    <div className="text-[10px] text-ink-dim pl-4">
                      Selection disabled in current release • Commercial premium tier
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Footer with key help */}
          <div className="mt-2 pt-2 border-t border-edge/60 px-2 flex items-center justify-between text-[10px] text-ink-dim">
            <span>Free keys take ~1 minute to generate</span>
            <a
              href="https://aistudio.google.com/app/apikey"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline flex items-center gap-0.5"
            >
              Get Gemini Key <ExternalLink className="size-2.5" />
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
