import { create } from "zustand";
import type { AIModel, ModelsResponse } from "@/types/model";

interface ModelState {
  selectedModelId: string;
  modelsData: ModelsResponse | null;
  setSelectedModelId: (modelId: string) => void;
  setModelsData: (data: ModelsResponse) => void;
  getSelectedModel: () => AIModel | undefined;
}

const DEFAULT_MODEL = "gemini-2.5-flash";
const STORAGE_KEY = "buildwise_selected_model_id";

function getInitialModelId(): string {
  if (typeof window !== "undefined") {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return saved;
  }
  return DEFAULT_MODEL;
}

export const useModelStore = create<ModelState>((set, get) => ({
  selectedModelId: getInitialModelId(),
  modelsData: null,

  setSelectedModelId: (modelId: string) => {
    if (typeof window !== "undefined") {
      localStorage.setItem(STORAGE_KEY, modelId);
    }
    set({ selectedModelId: modelId });
  },

  setModelsData: (data: ModelsResponse) => {
    set((state) => {
      // If current selectedModelId isn't valid, fallback to backend default
      const isValid = data.models.some(
        (m) => m.id === state.selectedModelId && m.is_selectable
      );
      const newModelId = isValid ? state.selectedModelId : (data.default_model_id || DEFAULT_MODEL);
      if (typeof window !== "undefined" && newModelId !== state.selectedModelId) {
        localStorage.setItem(STORAGE_KEY, newModelId);
      }
      return { modelsData: data, selectedModelId: newModelId };
    });
  },

  getSelectedModel: () => {
    const { modelsData, selectedModelId } = get();
    if (!modelsData) return undefined;
    return modelsData.models.find((m) => m.id === selectedModelId);
  },
}));
