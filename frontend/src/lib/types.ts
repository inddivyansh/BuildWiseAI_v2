// Mirrors the backend results payload (GET /projects/{id}/results).

export type Verdict = "COMPLIES_WITH" | "VIOLATES" | "NEEDS_REVIEW";

export interface VerdictEdge {
  verdict: Verdict;
  run_id: string;
  measured: number | null;
  required: string | null;
  reason: string;
  regulation_id: string;
  clause_id: string;
  clause_page: number;
  clause_bbox: number[] | null;
  clause_document_id: string;
}

export interface SheetRef {
  kind: "section" | "detail" | "elevation";
  detail_num: string;
  target_sheet_number: string | null;
  target_sheet_id: string;
  confidence?: number;
}

export interface Spec {
  code: string;
  category: string;
  description: string;
}

export interface DetailRef {
  sheet_number: string;
  number: string;
  title: string;
  bbox: number[];
  kind: string;
  measurements: Record<string, number>;
  notes: string[];
  target_sheet_id: string | null;
}

export interface Asset {
  id: string;
  type: string;
  label: string;
  bbox: [number, number, number, number];
  confidence: number;
  source: "vector-snapped" | "raster-snapped" | "vlm-only" | "schedule" | "detail-referenced";
  measurements: Record<string, number>;
  verdicts: VerdictEdge[];
  references: SheetRef[];
  specs: Spec[];
  details: DetailRef[];
}

export interface Sheet {
  id: string;
  document_id: string;
  page_number: number;
  width: number;
  height: number;
  scale_text: string | null;
  scale_in_per_point: number | null;
  sheet_number: string | null;
  title: string | null;
  assets: Asset[];
}

export interface Clause {
  id: string;
  clause_id: string;
  title: string;
  hierarchy_path: string;
  text: string;
  page: number;
  bbox: number[] | null;
  document_id: string;
}

export interface Doc {
  id: string;
  kind: "floorplan" | "codebook";
  filename: string;
  pdf_type: string | null;
  ingested: boolean;
}

export interface Results {
  project: { id: string; name: string };
  documents: Doc[];
  sheets: Sheet[];
  clauses: Clause[];
}

export interface RunEvent {
  stage: string;
  message: string;
  progress: number | null;
  level: "info" | "warning" | "error";
  asset_id: string | null;
}

export interface ProjectSummary {
  id: string;
  name: string;
  description?: string;
  created_at?: string;
  document_count: number;
  blueprint_count?: number;
}
