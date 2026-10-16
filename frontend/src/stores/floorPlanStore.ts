import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { GeneratedFloorPlan, ComplianceResult } from "@/types/floorPlan";

interface FloorPlanState {
  currentPlan: GeneratedFloorPlan | null;
  compliance: ComplianceResult | null;
  selectedRoomId: string | null;
  hoveredRoomId: string | null;
  viewMode: "2d" | "3d";
  isGenerating: boolean;
  generationError: string | null;
  history: GeneratedFloorPlan[];
  historyIndex: number;
}

interface FloorPlanActions {
  setPlan: (plan: GeneratedFloorPlan) => void;
  setCompliance: (result: ComplianceResult | null) => void;
  selectRoom: (roomId: string | null) => void;
  hoverRoom: (roomId: string | null) => void;
  setViewMode: (mode: "2d" | "3d") => void;
  setGenerating: (status: boolean) => void;
  setGenerationError: (error: string | null) => void;
  undo: () => void;
  redo: () => void;
  reset: () => void;
}

const initialState: FloorPlanState = {
  currentPlan: null,
  compliance: null,
  selectedRoomId: null,
  hoveredRoomId: null,
  viewMode: "2d",
  isGenerating: false,
  generationError: null,
  history: [],
  historyIndex: -1,
};

export const useFloorPlanStore = create<FloorPlanState & FloorPlanActions>()(
  immer((set) => ({
    ...initialState,

    setPlan: (plan) =>
      set((s) => {
        // Trim any future history after current index
        const newHistory = s.history.slice(0, s.historyIndex + 1);
        newHistory.push(plan);
        // Cap at 20 entries
        if (newHistory.length > 20) newHistory.shift();
        s.history = newHistory;
        s.historyIndex = newHistory.length - 1;
        s.currentPlan = plan;
        s.isGenerating = false;
        s.generationError = null;
      }),

    setCompliance: (result) =>
      set((s) => {
        s.compliance = result;
      }),

    selectRoom: (roomId) =>
      set((s) => {
        s.selectedRoomId = roomId;
      }),

    hoverRoom: (roomId) =>
      set((s) => {
        s.hoveredRoomId = roomId;
      }),

    setViewMode: (mode) =>
      set((s) => {
        s.viewMode = mode;
      }),

    setGenerating: (status) =>
      set((s) => {
        s.isGenerating = status;
        if (status) s.generationError = null;
      }),

    setGenerationError: (error) =>
      set((s) => {
        s.generationError = error;
        s.isGenerating = false;
      }),

    undo: () =>
      set((s) => {
        if (s.historyIndex > 0) {
          s.historyIndex -= 1;
          s.currentPlan = s.history[s.historyIndex];
        }
      }),

    redo: () =>
      set((s) => {
        if (s.historyIndex < s.history.length - 1) {
          s.historyIndex += 1;
          s.currentPlan = s.history[s.historyIndex];
        }
      }),

    reset: () => set(() => ({ ...initialState })),
  })),
);
