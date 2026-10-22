"use client";

import React, { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Sparkles,
  ChevronDown,
  Check,
  AlertTriangle,
  HardDrive,
  ExternalLink,
} from "lucide-react";
import { getAvailableModels } from "@/lib/api";
import { useModelStore } from "@/stores/modelStore";
import type { AIModel } from "@/types/model";
import { toast } from "sonner";

interface ModelSelectorProps {
  compact?: boolean;
  className?: string;
  onModelChange?: (model: AIModel) => void;
}

type CategoryFilter = "all" | "gemini" | "ollama";

export function ModelSelector({
  compact = false,
  className = "",
  onModelChange,
}: ModelSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeCategory, setActiveCategory] = useState<CategoryFilter>("all");
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

  const handleSelect = (model: AIModel) => {
    if (!model.is_selectable) {
      toast.info(`'${model.name}' is currently unavailable.`);
      return;
    }

    if (model.provider === "ollama") {
      toast.success(`Active Local Model: ${model.name}`, {
        description: "Connects to your local Ollama daemon (localhost:11434).",
      });
    } else {
      toast.success(`Active Model: ${model.name}`);
    }

    setSelectedModelId(model.id);
    onModelChange?.(model);
    setIsOpen(false);
  };

  const getProviderIcon = (provider: string) => {
    switch (provider) {
      case "gemini":
        return <Sparkles className="size-3.5 text-blue-400" />;
      case "ollama":
        return <HardDrive className="size-3.5 text-emerald-400" />;
      default:
        return <Sparkles className="size-3.5 text-accent" />;
    }
  };

  const categoriesOrder = ["gemini", "ollama"];
  const categoryMeta: Record<string, { title: string; badge: string; subtitle: string }> = {
    gemini: {
      title: "Google Gemini",
      badge: "Auto-Failover",
      subtitle: "Free Tier • Auto-shifts models if quota is reached",
    },
    ollama: {
      title: "Local Models (Ollama)",
      badge: "Local (Offline)",
      subtitle: "100% Private Offline (Qwen & Gemma)",
    },
  };

  const filteredModels = allModels.filter((m) => {
    if (activeCategory === "gemini") return m.provider === "gemini";
    if (activeCategory === "ollama") return m.provider === "ollama";
    return true;
  });

  const groupedCategories = categoriesOrder
    .map((catKey) => {
      const models = filteredModels.filter((m) => (m.category || m.provider) === catKey);
      return {
        key: catKey,
        info: categoryMeta[catKey] || { title: catKey.toUpperCase(), badge: "Free", subtitle: "" },
        models,
      };
    })
    .filter((g) => g.models.length > 0);

  const geminiCount = allModels.filter((m) => m.provider === "gemini").length;
  const ollamaCount = allModels.filter((m) => m.provider === "ollama").length;

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
        title="Select AI Model (Google Gemini & Local Ollama)"
      >
        <span className="flex items-center gap-1.5 truncate max-w-[220px]">
          {selectedModel ? (
            <>
              {getProviderIcon(selectedModel.provider)}
              <span className="truncate">{selectedModel.name}</span>
              <span
                className={`text-[9px] px-1 py-0.2 rounded font-semibold shrink-0 uppercase tracking-wider ${
                  selectedModel.provider === "ollama"
                    ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
                    : "bg-blue-500/15 text-blue-400 border border-blue-500/30"
                }`}
              >
                {selectedModel.provider === "ollama" ? "Local" : "Auto-Failover"}
              </span>
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
              selectedModel.is_available
                ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)]"
                : "bg-amber-500"
            }`}
            title={selectedModel.is_available ? "Ready & Available" : `Needs configuration`}
          />
        )}

        <ChevronDown className="size-3 text-ink-dim shrink-0 opacity-70" />
      </button>

      {/* Popover / Dropdown Menu */}
      {isOpen && (
        <div className="absolute bottom-full mb-1.5 left-0 z-50 w-84 sm:w-[400px] rounded-xl border border-edge bg-surface-1 p-2.5 text-ink shadow-2xl backdrop-blur-xl animate-in fade-in-0 zoom-in-95">
          {/* Header */}
          <div className="px-1.5 py-1 border-b border-edge/60 mb-2 flex items-center justify-between">
            <div>
              <div className="text-[11px] font-semibold text-ink-bright uppercase tracking-wider flex items-center gap-1.5">
                <Sparkles className="size-3 text-accent" />
                AI Model Engine
              </div>
              <div className="text-[10px] text-ink-dim">
                Google Gemini (Auto-Failover) & 2 Local Models (Qwen, Gemma)
              </div>
            </div>
            <span className="text-emerald-400 font-medium bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20 text-[10px]">
              100% Free
            </span>
          </div>

          {/* Category Filter Pills */}
          <div className="flex items-center gap-1 px-1 mb-2">
            <button
              type="button"
              onClick={() => setActiveCategory("all")}
              className={`text-[10px] font-medium px-2 py-0.5 rounded-md transition-all ${
                activeCategory === "all"
                  ? "bg-surface-2 text-ink-bright shadow-sm border border-edge"
                  : "text-ink-dim hover:text-ink"
              }`}
            >
              All Models ({allModels.length})
            </button>
            <button
              type="button"
              onClick={() => setActiveCategory("gemini")}
              className={`text-[10px] font-medium px-2 py-0.5 rounded-md transition-all flex items-center gap-1 ${
                activeCategory === "gemini"
                  ? "bg-blue-500/15 text-blue-400 shadow-sm border border-blue-500/30"
                  : "text-ink-dim hover:text-blue-400"
              }`}
            >
              <Sparkles className="size-2.5 text-blue-400" />
              Google Gemini ({geminiCount})
            </button>
            <button
              type="button"
              onClick={() => setActiveCategory("ollama")}
              className={`text-[10px] font-medium px-2 py-0.5 rounded-md transition-all flex items-center gap-1 ${
                activeCategory === "ollama"
                  ? "bg-emerald-500/15 text-emerald-400 shadow-sm border border-emerald-500/30"
                  : "text-ink-dim hover:text-emerald-400"
              }`}
            >
              <HardDrive className="size-2.5 text-emerald-400" />
              Ollama Local ({ollamaCount})
            </button>
          </div>

          <div className="max-h-[380px] overflow-y-auto space-y-3 pr-1 text-xs">
            {groupedCategories.map((group) => (
              <div key={group.key} className="space-y-1">
                {/* Category Header */}
                <div className="px-2 py-0.5 text-[10px] font-semibold text-ink-dim uppercase tracking-wider flex items-center justify-between border-b border-edge/30 pb-1">
                  <div className="flex items-center gap-1.5">
                    {getProviderIcon(group.key)}
                    <span className="text-ink-bright font-medium">{group.info.title}</span>
                  </div>
                  <span className="text-[9px] px-1.5 py-0.2 rounded-full font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/25">
                    {group.info.badge}
                  </span>
                </div>

                {/* Models in Category */}
                <div className="space-y-1">
                  {group.models.map((model) => {
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
                            <span className="font-semibold text-ink-bright text-xs">{model.name}</span>
                            <span
                              className={`inline-block size-1.5 rounded-full ${
                                model.is_available ? "bg-emerald-500" : "bg-amber-500"
                              }`}
                              title={model.is_available ? "Available" : "Needs setup"}
                            />
                          </div>

                          <div className="flex items-center gap-1 shrink-0">
                            <span className="text-[9px] px-1.5 py-0.2 rounded font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                              {model.badge}
                            </span>
                            {isSelected && <Check className="size-3 text-accent ml-1" />}
                          </div>
                        </div>

                        <div className="text-[11px] text-ink-dim leading-snug line-clamp-1">
                          {model.capabilities}
                        </div>

                        {/* Local Ollama helper note */}
                        {model.provider === "ollama" && (
                          <div className="text-[10px] text-emerald-400/90 flex items-center gap-1 mt-0.5 bg-emerald-500/10 px-1.5 py-0.5 rounded">
                            <HardDrive className="size-2.5 shrink-0" />
                            <span>Zero API keys • Runs offline on localhost:11434</span>
                          </div>
                        )}

                        {/* Gemini auto-failover helper note */}
                        {model.provider === "gemini" && model.is_available && (
                          <div className="text-[10px] text-blue-400/90 flex items-center gap-1 mt-0.5 bg-blue-500/10 px-1.5 py-0.5 rounded">
                            <Sparkles className="size-2.5 shrink-0" />
                            <span>Auto-shifts to next Gemini model if quota or rate limit is reached</span>
                          </div>
                        )}

                        {/* Gemini key note if unconfigured */}
                        {model.provider === "gemini" && !model.is_available && (
                          <div className="text-[10px] text-amber-400/90 flex items-center gap-1 mt-0.5 bg-amber-500/10 px-1.5 py-0.5 rounded">
                            <AlertTriangle className="size-2.5 shrink-0" />
                            <span>Requires GEMINI_API_KEY in .env</span>
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          {/* Footer with key help */}
          <div className="mt-2 pt-2 border-t border-edge/60 px-2 flex items-center justify-between text-[10px] text-ink-dim">
            <span>Free: Google Gemini (Auto-Failover) & 2 Local Models</span>
            <a
              href="https://aistudio.google.com/app/apikey"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline flex items-center gap-0.5"
            >
              AI Studio Free Key <ExternalLink className="size-2.5" />
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
