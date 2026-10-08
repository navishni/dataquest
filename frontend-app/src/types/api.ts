// Types for cross-cutting endpoints (config, auth, batches, sources, cases, jobs, metrics, health).
import type { ApiError, EvidenceReference, ID, ISODate, WarningItem } from "./canonical";

export interface Option { id: string; label: string; description?: string }
export interface ConfidenceBand { id: string; min: number; max: number; label: string }

export interface AppConfig {
  poll_interval_ms: number;
  realtime_url?: string;
  auth: { mode: string; login_url?: string };
  processing_modes: Option[];
  output_formats: Option[];
  export_scopes: Option[];
  action_types: Option[];
  limits: { max_file_mb: number; max_pages: number; accepted_types: string[] };
  confidence_bands: ConfidenceBand[];
  severity_levels: Option[];
  feature_flags: Record<string, boolean>;
  pipeline_stages: Option[];
}

export interface Me { user_id: string; display_name: string; role: string; capabilities: string[]; tenant_id?: string }

export interface JobStatus {
  job_id: ID; status: string; stage?: string; progress_percent?: number; result?: unknown; error?: ApiError;
}

export interface BatchSource {
  source_id: ID; filename: string; status: string; stage?: string; progress_percent?: number;
  warnings: WarningItem[]; errors: ApiError[]; retryable?: boolean;
}
export interface Batch {
  batch_id: ID; name?: string; case_id?: ID; created_at?: ISODate; job_id?: ID; sources: BatchSource[];
}
export interface BatchCreateInput {
  source_ids: ID[]; mode: string; output_formats: string[]; case_id?: ID; instruction?: string; options: Record<string, boolean>;
}
export interface BatchCreateOutput { batch_id: ID; job_id?: ID }

export interface CaseSummary { case_id: ID; name: string; status?: string; created_at?: ISODate }
export interface CaseCreateInput { name: string }

export interface MetricItem { id: string; label: string; value: number; unit?: string; series?: { t: ISODate; v: number }[] }
export interface MetricsOutput { items: MetricItem[] }
export interface HealthOutput { agents: { id: string; name: string; status: string; latency_ms?: number }[] }

export interface MarkdownOutput { markdown: string }

export type { EvidenceReference };
