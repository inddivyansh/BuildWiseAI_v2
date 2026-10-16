"use client";

import React, { useState, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Send, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { useFloorPlanStore } from "@/stores/floorPlanStore";
import { useGenerateAction, useEditAction, useComplianceCheck } from "@/hooks/useFloorPlanActions";
import { api } from "@/lib/api";

export function NLInput({ projectId }: { projectId?: string } = {}) {
  const router = useRouter();
  const [text, setText] = useState("");
  const { currentPlan, isGenerating } = useFloorPlanStore();

  useEffect(() => {
    if (typeof window !== "undefined") {
      const p = new URLSearchParams(window.location.search).get("prompt");
      if (p) {
        setText(p);
      }
    }
  }, []);

  const generateAction = useGenerateAction();
  const editAction = useEditAction();
  const complianceCheck = useComplianceCheck();

  const isEdit = !!currentPlan;
  const isPending = generateAction.isPending || editAction.isPending;

  const handleSubmit = useCallback(async () => {
    const trimmed = text.trim();
    if (!trimmed || isPending) return;

    if (isEdit) {
      editAction.mutate(
        {
          generation_id: currentPlan!.generation_id,
          prompt: trimmed,
        },
        {
          onSuccess: () => {
            setText("");
            // Auto-run compliance after edit
            complianceCheck.mutate({});
          },
        },
      );
    } else {
      let targetProjectId = projectId;
      if (!targetProjectId) {
        try {
          const title = trimmed.length > 30 ? `${trimmed.slice(0, 30)}...` : trimmed;
          const newProj = await api.createProject(
            title,
            `Created from prompt: "${trimmed.slice(0, 80)}"`
          );
          targetProjectId = newProj.id;
        } catch {
          // offline/local fallback
        }
      }

      generateAction.mutate(
        { prompt: trimmed, project_id: targetProjectId },
        {
          onSuccess: (data) => {
            setText("");
            const bpId = data.floor_plan?.generation_id || data.generation_id;
            if (targetProjectId) {
              router.replace(`/editor/${targetProjectId}?blueprintId=${bpId}`);
            }
          },
        },
      );
    }
  }, [text, isPending, isEdit, currentPlan, editAction, generateAction, complianceCheck, projectId, router]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="border-t border-border/50 bg-background/80 backdrop-blur-sm px-4 py-3">
      {/* Loading indicator */}
      {isGenerating && (
        <div className="mb-2">
          <Progress value={null} className="h-1" />
          <p className="text-xs text-muted-foreground mt-1">
            {isEdit ? "Editing floor plan..." : "Generating floor plan..."}
          </p>
        </div>
      )}

      <div className="flex items-center gap-2">
        <Badge
          variant={isEdit ? "default" : "secondary"}
          className="shrink-0 text-[10px]"
        >
          {isEdit ? "Edit" : "Generate"}
        </Badge>

        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            isEdit
              ? "Describe changes to the current plan..."
              : "Describe a floor plan to generate..."
          }
          disabled={isPending}
          className="flex-1 bg-transparent border-none outline-none text-sm placeholder:text-muted-foreground/60"
        />

        <Button
          size="icon"
          variant="ghost"
          className="h-8 w-8 shrink-0"
          disabled={isPending || !text.trim()}
          onClick={handleSubmit}
        >
          {isPending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Send size={14} />
          )}
        </Button>
      </div>

      <p className="text-[10px] text-muted-foreground/50 mt-1">
        Ctrl+Enter to submit
      </p>
    </div>
  );
}
