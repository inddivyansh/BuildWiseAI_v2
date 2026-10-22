// Single unified HTTP client for BuildwiseAI (PlanLint + ArchiGen).
// Provides both `api` object (PlanLint) and named exports (ArchiGen).

import type {
  GenerateRequest,
  GenerateResponse,
  EditRequest,
  EditResponse,
  GeneratedFloorPlan,
  ComplianceResult,
  FloorPlanSpec,
} from "@/types/floorPlan";
import type { ModelsResponse } from "@/types/model";
import type { Results, RunEvent, ProjectSummary as PlanLintProjectSummary } from "./types";

import { auth } from "./auth";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function getAuthHeaders(): Record<string, string> {
  const token = auth.getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function handle401Unauthorized() {
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    auth.signOut();
    const fullPath = window.location.pathname + window.location.search;
    const redirect = fullPath !== "/" ? `?redirect=${encodeURIComponent(fullPath)}` : "";
    window.location.href = `/login${redirect}`;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = path.startsWith("http") ? path : `${API_URL}${path}`;
  const headers = {
    ...getAuthHeaders(),
    ...(init?.headers || {}),
  };
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    handle401Unauthorized();
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? body.message ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

async function requestBlob(path: string): Promise<Blob> {
  const url = path.startsWith("http") ? path : `${API_URL}${path}`;
  const res = await fetch(url, {
    headers: getAuthHeaders(),
  });
  if (res.status === 401) {
    handle401Unauthorized();
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }
  return res.blob();
}

function json(body: unknown, method = "POST"): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

async function authFetch(url: string, init?: RequestInit): Promise<Response> {
  const headers = {
    ...getAuthHeaders(),
    ...(init?.headers || {}),
  };
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    handle401Unauthorized();
  }
  return res;
}

async function jsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function assertOk(response: Response): Promise<void> {
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
}

// ---------------------------------------------------------------------------
// ArchiGen Named Endpoints
// ---------------------------------------------------------------------------

export function generateFloorPlan(req: GenerateRequest) {
  return request<GenerateResponse>("/api/generate", json(req));
}

export function generateFromSpec(spec: FloorPlanSpec, projectId?: string) {
  const path = projectId ? `/api/generate/from-spec?project_id=${projectId}` : "/api/generate/from-spec";
  return request<GenerateResponse>(path, json(spec));
}

export function editFloorPlan(req: EditRequest) {
  return request<EditResponse>("/api/edit", json(req));
}

export function getAvailableModels() {
  return request<ModelsResponse>("/api/models");
}

export interface IngestResponse {
  generation_id: string;
  status: "completed" | "failed";
  floor_plan?: GeneratedFloorPlan;
  confidence?: number;
  warnings: string[];
  error?: string;
  project_id?: string;
}

export function ingestFile(
  file: File,
  opts?: {
    file_type?: string;
    project_id?: string;
    scale_m_per_pixel?: number;
    plot_width_m?: number;
    plot_depth_m?: number;
    locale?: string;
  },
) {
  const fd = new FormData();
  fd.append("file", file);
  if (opts?.file_type) fd.append("file_type", opts.file_type);
  if (opts?.project_id) fd.append("project_id", opts.project_id);
  if (opts?.scale_m_per_pixel != null) fd.append("scale_m_per_pixel", String(opts.scale_m_per_pixel));
  if (opts?.plot_width_m != null) fd.append("plot_width_m", String(opts.plot_width_m));
  if (opts?.plot_depth_m != null) fd.append("plot_depth_m", String(opts.plot_depth_m));
  if (opts?.locale) fd.append("locale", opts.locale);
  return request<IngestResponse>("/api/ingest", { method: "POST", body: fd });
}

export interface ExportFormat {
  id: string;
  name: string;
  description: string;
}

export function getExportFormats() {
  return request<{ formats: ExportFormat[] }>("/api/export/formats");
}

export async function exportPlan(generationId: string, format: string): Promise<Blob> {
  return requestBlob(`/api/export/${generationId}/${format}`);
}

export interface ComplianceCheckRequest {
  floor_plan: GeneratedFloorPlan;
  zone_id?: string;
  locale?: string;
  run_llm?: boolean;
}

export function checkCompliance(req: ComplianceCheckRequest) {
  return request<ComplianceResult>("/api/compliance/check", json(req));
}

export function checkSpecCompliance(spec: FloorPlanSpec, zone_id?: string) {
  return request<ComplianceResult>("/api/compliance/check-spec", json({ spec, zone_id }));
}

export interface ZoneSpec {
  zone_id: string;
  authorized_uses: string[];
  max_height_m?: number;
  front_margin_m: number;
  side_margin_m: number;
  rear_margin_m: number;
  min_lot_area_sqm: number;
  min_green_area_pct: number;
  max_density_units_per_ha?: number;
  parking_type: string;
  min_underground_parking_pct: number;
  referenced_articles: string[];
  special_provisions: string[];
}

export function listZones() {
  return request<{ zones: string[]; count: number }>("/api/compliance/zones");
}

export function getZone(zoneId: string) {
  return request<ZoneSpec>(`/api/compliance/zones/${zoneId}`);
}

export function searchClauses(q: string, opts?: { jurisdiction?: string; limit?: number }) {
  const params = new URLSearchParams({ q });
  if (opts?.jurisdiction) params.set("jurisdiction", opts.jurisdiction);
  if (opts?.limit) params.set("limit", String(opts.limit));
  return request<{ results: Record<string, unknown>[]; count: number }>(`/api/compliance/search?${params}`);
}

export interface ProjectSummary {
  id: string;
  name: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
  thumbnail_url?: string;
  document_count?: number;
  blueprint_count?: number;
  version_count?: number;
}

export interface ProjectDetail extends ProjectSummary {
  latest_floor_plan?: GeneratedFloorPlan;
  compliance?: ComplianceResult;
}

export interface ProjectCreate {
  name: string;
  description?: string;
}

export function listProjects() {
  return request<ProjectSummary[]>("/api/projects");
}

export function createProject(data: ProjectCreate) {
  return request<ProjectSummary>("/api/projects", json(data));
}

export async function getProject(projectId: string, blueprintId?: string): Promise<ProjectDetail> {
  const p = await request<ProjectDetail>(`/api/projects/${projectId}`);
  try {
    const blueprints = await request<
      { id: string; generator_model: string; total_area_sqm: number; created_at: string }[]
    >(`/api/projects/${projectId}/blueprints`);
    if (blueprints && blueprints.length > 0) {
      const targetId = blueprintId || blueprints[0].id;
      const bp = await request<{
        id: string;
        floor_plan: GeneratedFloorPlan;
        compliance?: ComplianceResult;
      }>(`/api/projects/${projectId}/blueprints/${targetId}`);
      if (bp && bp.floor_plan) {
        p.latest_floor_plan = bp.floor_plan;
        p.compliance = bp.compliance;
      }
    }
  } catch {
    // If no blueprints yet, return base project details
  }
  return p;
}

export async function sendBlueprintToComplianceAudit(
  projectId: string,
  plan: GeneratedFloorPlan,
  projectName?: string,
): Promise<{ document_id: string; project_id: string }> {
  const { createFloorPlanPdfBlob } = await import("./pdfExport");
  const blob = createFloorPlanPdfBlob(plan, { projectName });
  const filename = `blueprint_${plan.generation_id.slice(0, 8)}.pdf`;
  const file = new File([blob], filename, { type: "application/pdf" });
  const doc = await api.uploadDocument(projectId, "floorplan", file);
  return { document_id: doc.id, project_id: projectId };
}

export async function deleteProject(projectId: string): Promise<void> {
  return authFetch(`${API_URL}/projects/${projectId}`, { method: "DELETE" }).then(assertOk);
}

export interface BlueprintSummary {
  id: string;
  generator_model: string;
  total_area_sqm: number;
  created_at: string;
}

export interface BlueprintDetail extends BlueprintSummary {
  floor_plan: GeneratedFloorPlan;
  compliance?: ComplianceResult;
}

export function listBlueprints(projectId: string) {
  return request<BlueprintSummary[]>(`/api/projects/${projectId}/blueprints`);
}

export function getBlueprint(projectId: string, blueprintId: string) {
  return request<BlueprintDetail>(`/api/projects/${projectId}/blueprints/${blueprintId}`);
}

export function deleteBlueprint(projectId: string, blueprintId: string) {
  return request<void>(`/api/projects/${projectId}/blueprints/${blueprintId}`, {
    method: "DELETE",
  });
}

export function healthCheck() {
  return request<{ status: string; version: string }>("/health");
}

// ---------------------------------------------------------------------------
// PlanLint Object Client (api)
// ---------------------------------------------------------------------------

export interface RunHandlers {
  onEvent: (event: RunEvent) => void;
  /** Run finished successfully (stage === "done"). */
  onDone: () => void;
  /** Run failed (stage === "error") or the connection dropped. */
  onError: (message: string) => void;
}

export const api = {
  listProjects: () =>
    authFetch(`${API_URL}/projects`).then((r) => jsonResponse<PlanLintProjectSummary[]>(r)),

  createProject: (name: string, description?: string) =>
    authFetch(`${API_URL}/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    }).then((r) => jsonResponse<{ id: string; name: string; description?: string }>(r)),

  getProject: (projectId: string) =>
    authFetch(`${API_URL}/projects/${projectId}`).then((r) => jsonResponse<ProjectSummary>(r)),

  updateProject: (projectId: string, data: { name?: string; description?: string }) =>
    authFetch(`${API_URL}/projects/${projectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }).then((r) => jsonResponse<ProjectSummary>(r)),

  deleteProject: (projectId: string) =>
    authFetch(`${API_URL}/projects/${projectId}`, { method: "DELETE" }).then(assertOk),

  deleteDocument: (documentId: string) =>
    authFetch(`${API_URL}/documents/${documentId}`, { method: "DELETE" }).then(assertOk),

  listDocuments: (projectId: string) =>
    authFetch(`${API_URL}/projects/${projectId}/documents`).then((r) => jsonResponse<any[]>(r)),

  listRuns: (projectId: string) =>
    authFetch(`${API_URL}/projects/${projectId}/runs`).then((r) => jsonResponse<any[]>(r)),

  uploadDocument: (projectId: string, kind: "floorplan" | "codebook", file: File) => {
    const body = new FormData();
    body.append("file", file);
    return authFetch(
      `${API_URL}/projects/${projectId}/documents?kind=${kind}`,
      { method: "POST", body },
    ).then((r) => jsonResponse<{ id: string }>(r));
  },

  startVerification: (projectId: string) =>
    authFetch(`${API_URL}/projects/${projectId}/verify`, { method: "POST" }).then(
      (r) => jsonResponse<{ run_id: string }>(r),
    ),

  results: (projectId: string, runId?: string) => {
    const url = runId
      ? `${API_URL}/projects/${projectId}/results?run_id=${runId}`
      : `${API_URL}/projects/${projectId}/results`;
    return authFetch(url).then((r) => jsonResponse<Results>(r));
  },

  setDocumentScale: (documentId: string, scaleText: string) =>
    authFetch(`${API_URL}/documents/${documentId}/scale`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scale_text: scaleText }),
    }).then((r) => jsonResponse<{ status: string }>(r)),

  pdfUrl: (documentId: string) => {
    const token = auth.getToken();
    return token
      ? `${API_URL}/documents/${documentId}/pdf?token=${encodeURIComponent(token)}`
      : `${API_URL}/documents/${documentId}/pdf`;
  },

  listBlueprints: (projectId: string) =>
    authFetch(`${API_URL}/projects/${projectId}/blueprints`).then((r) => jsonResponse<BlueprintSummary[]>(r)),

  getBlueprint: (projectId: string, blueprintId: string) =>
    authFetch(`${API_URL}/projects/${projectId}/blueprints/${blueprintId}`).then((r) => jsonResponse<BlueprintDetail>(r)),

  deleteBlueprint: (projectId: string, blueprintId: string) =>
    authFetch(`${API_URL}/projects/${projectId}/blueprints/${blueprintId}`, { method: "DELETE" }).then(assertOk),

  /** Subscribe to run progress. Returns an unsubscribe function. */
  subscribeToRun(runId: string, handlers: RunHandlers): () => void {
    const token = auth.getToken();
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    const source = new EventSource(`${API_URL}/runs/${runId}/events${query}`);
    let settled = false;

    const settle = (fn: () => void) => {
      if (settled) return;
      settled = true;
      source.close();
      fn();
    };

    source.addEventListener("progress", (message) => {
      let event: RunEvent;
      try {
        event = JSON.parse((message as MessageEvent).data) as RunEvent;
      } catch {
        settle(() => handlers.onError("received a malformed progress event"));
        return;
      }
      handlers.onEvent(event);
      if (event.stage === "done") {
        settle(handlers.onDone);
      } else if (event.stage === "error") {
        settle(() => handlers.onError(event.message));
      }
    });
    source.onerror = () => {
      settle(() => handlers.onError("connection to the server was lost"));
    };
    return () => {
      settled = true;
      source.close();
    };
  },
};
