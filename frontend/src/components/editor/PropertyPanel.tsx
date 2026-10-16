"use client";

import React from "react";
import { X, RefreshCw, AlertTriangle, CheckCircle2, Info, FileCheck2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { useFloorPlanStore } from "@/stores/floorPlanStore";
import { useEditorStore } from "@/stores/editorStore";
import { useComplianceCheck, useSendToAuditAction } from "@/hooks/useFloorPlanActions";
import { ROOM_COLORS, type ComplianceViolation } from "@/types/floorPlan";
import { polygonArea } from "@/lib/geometry";

export function PropertyPanel({ projectId }: { projectId?: string } = {}) {
  const { rightPanelTab, setRightPanelTab, setRightPanelOpen } = useEditorStore();

  return (
    <aside className="w-80 border-l border-border/50 bg-background/80 backdrop-blur-sm flex flex-col shrink-0">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/50">
        <Tabs value={rightPanelTab} onValueChange={(v) => setRightPanelTab(v as typeof rightPanelTab)}>
          <TabsList className="h-7">
            <TabsTrigger value="properties" className="text-xs px-2 h-6">
              Properties
            </TabsTrigger>
            <TabsTrigger value="compliance" className="text-xs px-2 h-6">
              Compliance
            </TabsTrigger>
            <TabsTrigger value="settings" className="text-xs px-2 h-6">
              Settings
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          onClick={() => setRightPanelOpen(false)}
        >
          <X size={14} />
        </Button>
      </div>

      <ScrollArea className="flex-1">
        {rightPanelTab === "properties" && <PropertiesTab />}
        {rightPanelTab === "compliance" && <ComplianceTab projectId={projectId} />}
        {rightPanelTab === "settings" && <SettingsTab />}
      </ScrollArea>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Properties Tab
// ---------------------------------------------------------------------------

function PropertiesTab() {
  const { currentPlan, selectedRoomId, selectRoom } = useFloorPlanStore();

  if (!currentPlan) {
    return (
      <div className="p-4 text-sm text-muted-foreground text-center mt-8">
        No floor plan loaded
      </div>
    );
  }

  const selectedRoom = selectedRoomId
    ? currentPlan.spec.rooms.find((r) => r.id === selectedRoomId)
    : null;

  if (selectedRoom) {
    const colors = ROOM_COLORS[selectedRoom.type] ?? ROOM_COLORS.other;
    const actualArea = selectedRoom.actual_area_sqm ?? (selectedRoom.polygon ? polygonArea(selectedRoom.polygon) : 0);

    return (
      <div className="p-4 space-y-4">
        <div className="flex items-center gap-2">
          <div
            className="w-3 h-3 rounded-sm"
            style={{ backgroundColor: colors.dark }}
          />
          <Badge variant="secondary" className="text-xs">
            {selectedRoom.type.replace("_", " ")}
          </Badge>
        </div>

        <h3 className="font-medium text-sm">{selectedRoom.label}</h3>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <StatBox label="Target" value={`${selectedRoom.target_area_sqm.toFixed(1)} m²`} />
          <StatBox label="Actual" value={`${actualArea.toFixed(1)} m²`} />
          <StatBox label="Floor" value={`${selectedRoom.floor}`} />
          <StatBox
            label="Window"
            value={selectedRoom.requires_window ? "Required" : "No"}
          />
        </div>

        {selectedRoom.min_width_m && (
          <div className="text-xs text-muted-foreground">
            Min width: {selectedRoom.min_width_m}m | Min depth: {selectedRoom.min_depth_m ?? "—"}m
          </div>
        )}
      </div>
    );
  }

  // No room selected: show plan summary
  const { spec } = currentPlan;
  return (
    <div className="p-4 space-y-4">
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        Plan Summary
      </h3>

      <div className="grid grid-cols-2 gap-3 text-xs">
        <StatBox label="Total Area" value={`${currentPlan.total_area_sqm.toFixed(1)} m²`} />
        <StatBox label="Lot Coverage" value={`${(currentPlan.lot_coverage_ratio * 100).toFixed(0)}%`} />
        <StatBox label="Rooms" value={`${spec.rooms.length}`} />
        <StatBox label="Generator" value={currentPlan.generator_model} />
        <StatBox label="Plot" value={`${spec.plot.width_m}×${spec.plot.depth_m}m`} />
        <StatBox label="Time" value={`${currentPlan.generation_time_s.toFixed(1)}s`} />
      </div>

      <Separator />

      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        Rooms
      </h3>

      <div className="space-y-1">
        {spec.rooms.map((room) => {
          const colors = ROOM_COLORS[room.type] ?? ROOM_COLORS.other;
          return (
            <button
              key={room.id}
              className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-muted/50 text-left transition-colors"
              onClick={() => selectRoom(room.id)}
            >
              <div
                className="w-2.5 h-2.5 rounded-sm shrink-0"
                style={{ backgroundColor: colors.dark }}
              />
              <span className="text-xs truncate flex-1">{room.label}</span>
              <span className="text-xs text-muted-foreground tabular-nums">
                {(room.actual_area_sqm ?? room.target_area_sqm).toFixed(0)}m²
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compliance Tab
// ---------------------------------------------------------------------------

function ComplianceTab({ projectId }: { projectId?: string } = {}) {
  const { currentPlan, compliance, selectRoom } = useFloorPlanStore();
  const complianceCheck = useComplianceCheck();
  const sendToAudit = useSendToAuditAction();

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Zoning & Bylaws
          </h3>
          <p className="text-[10px] text-muted-foreground/60">
            Generation-time municipal limits
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={() => complianceCheck.mutate({})}
          disabled={complianceCheck.isPending}
        >
          <RefreshCw
            size={12}
            className={`mr-1 ${complianceCheck.isPending ? "animate-spin" : ""}`}
          />
          Re-check
        </Button>
      </div>

      {!compliance ? (
        <p className="text-xs text-muted-foreground text-center py-2">
          No zoning compliance results yet
        </p>
      ) : (
        <>
          <Badge
            variant={compliance.is_compliant ? "default" : "destructive"}
            className="w-full justify-center py-1"
          >
            {compliance.is_compliant
              ? "All zoning checks passed"
              : `${compliance.violations.length} zoning violation(s)`}
          </Badge>

          <p className="text-[11px] text-muted-foreground">
            {compliance.checked_rules} rules checked · {compliance.jurisdiction}
          </p>

          {compliance.violations.length > 0 && (
            <Accordion className="space-y-1">
              {compliance.violations.map((v, i) => (
                <ViolationItem key={i} violation={v} onRoomClick={selectRoom} />
              ))}
            </Accordion>
          )}
        </>
      )}

      <Separator />

      {/* PlanLint Full Regulatory Code Audit Section */}
      <div className="space-y-2.5 rounded-lg border border-border/50 bg-secondary/30 p-3">
        <div className="flex items-center gap-1.5">
          <FileCheck2 size={15} className="text-pass" />
          <span className="text-xs font-semibold text-foreground">
            PlanLint Regulatory Audit
          </span>
        </div>
        <p className="text-[11px] text-muted-foreground leading-relaxed">
          Verify building codes (ADA clearances, corridor egress, IBC/NBC rules) using dual-pane vector snapping against official codebooks.
        </p>
        <Button
          size="sm"
          onClick={() => sendToAudit.mutate({ projectId })}
          disabled={sendToAudit.isPending || !currentPlan}
          className="w-full bg-pass hover:bg-pass/90 text-surface-0 text-xs h-7.5 font-medium flex items-center justify-center gap-1.5 shadow-sm transition-colors"
        >
          {sendToAudit.isPending ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <FileCheck2 size={13} />
          )}
          <span>{sendToAudit.isPending ? "Exporting..." : "Send to Compliance Audit"}</span>
        </Button>
      </div>
    </div>
  );
}

function ViolationItem({
  violation,
  onRoomClick,
}: {
  violation: ComplianceViolation;
  onRoomClick: (id: string) => void;
}) {
  const Icon =
    violation.severity === "error"
      ? AlertTriangle
      : violation.severity === "warning"
        ? AlertTriangle
        : Info;

  const severityColor =
    violation.severity === "error"
      ? "text-red-400"
      : violation.severity === "warning"
        ? "text-yellow-400"
        : "text-blue-400";

  return (
    <AccordionItem value={violation.rule_id} className="border-border/30">
      <AccordionTrigger className="text-xs py-2 hover:no-underline">
        <div className="flex items-center gap-2">
          <Icon size={14} className={severityColor} />
          <Badge
            variant={violation.severity === "error" ? "destructive" : "secondary"}
            className="text-[10px] px-1.5"
          >
            {violation.severity}
          </Badge>
          <span className="truncate">{violation.rule_id}</span>
        </div>
      </AccordionTrigger>
      <AccordionContent className="text-xs space-y-2 pb-3">
        <p>{violation.description}</p>

        {violation.code_reference && (
          <p className="text-muted-foreground">
            Ref: {violation.code_reference}
          </p>
        )}

        {violation.affected_rooms.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {violation.affected_rooms.map((rid) => (
              <Badge
                key={rid}
                variant="outline"
                className="text-[10px] cursor-pointer hover:bg-muted"
                onClick={() => onRoomClick(rid)}
              >
                {rid}
              </Badge>
            ))}
          </div>
        )}

        {violation.suggestion && (
          <div className="p-2 rounded-md bg-muted/50 text-muted-foreground">
            <CheckCircle2 size={12} className="inline mr-1" />
            {violation.suggestion}
          </div>
        )}
      </AccordionContent>
    </AccordionItem>
  );
}

// ---------------------------------------------------------------------------
// Settings Tab
// ---------------------------------------------------------------------------

function SettingsTab() {
  const { showGrid, showDimensions, showSetbacks, toggleGrid, toggleDimensions, toggleSetbacks } =
    useEditorStore();
  const { currentPlan } = useFloorPlanStore();

  return (
    <div className="p-4 space-y-4">
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        Canvas
      </h3>

      <div className="space-y-2">
        <ToggleRow label="Show Grid" active={showGrid} onToggle={toggleGrid} />
        <ToggleRow label="Show Dimensions" active={showDimensions} onToggle={toggleDimensions} />
        <ToggleRow label="Show Setbacks" active={showSetbacks} onToggle={toggleSetbacks} />
      </div>

      {currentPlan && (
        <>
          <Separator />
          <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Plan Info
          </h3>
          <div className="text-xs space-y-1 text-muted-foreground">
            <p>Generator: {currentPlan.generator_model}</p>
            <p>ID: {currentPlan.generation_id.slice(0, 12)}...</p>
            {currentPlan.spec.zone_id && <p>Zone: {currentPlan.spec.zone_id}</p>}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function StatBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-2.5 rounded-lg bg-muted/50">
      <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-0.5">
        {label}
      </p>
      <p className="text-sm font-medium truncate">{value}</p>
    </div>
  );
}

function ToggleRow({
  label,
  active,
  onToggle,
}: {
  label: string;
  active: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      className="w-full flex items-center justify-between px-2 py-1.5 rounded-md hover:bg-muted/50 transition-colors"
      onClick={onToggle}
    >
      <span className="text-xs">{label}</span>
      <div
        className={`w-7 h-4 rounded-full transition-colors ${active ? "bg-foreground" : "bg-muted"}`}
      >
        <div
          className={`w-3 h-3 rounded-full bg-background transition-transform mt-0.5 ${active ? "translate-x-3.5" : "translate-x-0.5"}`}
        />
      </div>
    </button>
  );
}
