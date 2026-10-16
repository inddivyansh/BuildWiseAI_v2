import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

export type EditorTool = "select" | "pan" | "measure";
export type RightPanelTab = "properties" | "compliance" | "settings";

interface EditorState {
  activeTool: EditorTool;
  zoom: number;
  panOffset: { x: number; y: number };
  showGrid: boolean;
  showDimensions: boolean;
  showSetbacks: boolean;
  rightPanelTab: RightPanelTab;
  rightPanelOpen: boolean;
}

interface EditorActions {
  setTool: (tool: EditorTool) => void;
  setZoom: (zoom: number) => void;
  setPan: (offset: { x: number; y: number }) => void;
  toggleGrid: () => void;
  toggleDimensions: () => void;
  toggleSetbacks: () => void;
  setRightPanelTab: (tab: RightPanelTab) => void;
  setRightPanelOpen: (open: boolean) => void;
}

export const useEditorStore = create<EditorState & EditorActions>()(
  immer((set) => ({
    activeTool: "select",
    zoom: 1,
    panOffset: { x: 0, y: 0 },
    showGrid: true,
    showDimensions: true,
    showSetbacks: true,
    rightPanelTab: "properties",
    rightPanelOpen: true,

    setTool: (tool) =>
      set((s) => {
        s.activeTool = tool;
      }),

    setZoom: (zoom) =>
      set((s) => {
        s.zoom = Math.max(0.1, Math.min(10, zoom));
      }),

    setPan: (offset) =>
      set((s) => {
        s.panOffset = offset;
      }),

    toggleGrid: () =>
      set((s) => {
        s.showGrid = !s.showGrid;
      }),

    toggleDimensions: () =>
      set((s) => {
        s.showDimensions = !s.showDimensions;
      }),

    toggleSetbacks: () =>
      set((s) => {
        s.showSetbacks = !s.showSetbacks;
      }),

    setRightPanelTab: (tab) =>
      set((s) => {
        s.rightPanelTab = tab;
      }),

    setRightPanelOpen: (open) =>
      set((s) => {
        s.rightPanelOpen = open;
      }),
  })),
);
