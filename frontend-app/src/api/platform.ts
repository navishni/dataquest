// Typed clients for the cross-cutting endpoints (config, auth, batches, sources, cases, metrics, health).
import { z } from "zod";
import { apiRequest } from "./client";
import {
  apiErrorSchema, blockSchema, nn, pageUnitSchema, sourceDocumentSchema, warningSchema, type Schema,
} from "@/types/schemas";
import type {
  AppConfig, Batch, BatchCreateInput, BatchCreateOutput, CaseCreateInput, CaseSummary, HealthOutput, MarkdownOutput, Me,
  MetricsOutput,
} from "@/types/api";
import type { PageUnit, SourceDocument } from "@/types/canonical";

const option = z.object({ id: z.string(), label: z.string(), description: nn(z.string()) }).passthrough();

export const configSchema: Schema<AppConfig> = z
  .object({
    poll_interval_ms: z.number(),
    realtime_url: nn(z.string()),
    auth: z.object({ mode: z.string(), login_url: nn(z.string()) }).passthrough(),
    processing_modes: z.array(option),
    output_formats: z.array(option),
    export_scopes: z.array(option),
    action_types: z.array(option),
    limits: z.object({ max_file_mb: z.number(), max_pages: z.number(), accepted_types: z.array(z.string()) }).passthrough(),
    confidence_bands: z.array(z.object({ id: z.string(), min: z.number(), max: z.number(), label: z.string() }).passthrough()),
    severity_levels: z.array(option),
    feature_flags: z.record(z.boolean()),
    pipeline_stages: z.array(option),
  })
  .passthrough();

export const meSchema: Schema<Me> = z
  .object({
    user_id: z.string(), display_name: z.string(), role: z.string(), capabilities: z.array(z.string()), tenant_id: nn(z.string()),
  })
  .passthrough();

export const ENDPOINTS = {
  config: "/config",
  me: "/auth/me",
  login: "/auth/login",
  logout: "/auth/logout",
  batches: "/batches",
  sources: "/sources",
  cases: "/cases",
  metrics: "/metrics",
  health: "/health/agents",
} as const;

export const getConfig = (signal?: AbortSignal) => apiRequest<AppConfig>(ENDPOINTS.config, { schema: configSchema, signal });
export const getMe = (signal?: AbortSignal) => apiRequest<Me>(ENDPOINTS.me, { schema: meSchema, signal });

export type LoginRole = "admin" | "editor" | "viewer";
export interface LoginInput { username: string; password: string; role: LoginRole }
const loginSchema = z.object({ access_token: z.string() }).passthrough();
export const login = (path: string, input: LoginInput) =>
  apiRequest<{ access_token: string }>(path, { method: "POST", json: input, schema: loginSchema });
export const logout = () => apiRequest<unknown>(ENDPOINTS.logout, { method: "POST" });

const batchSource = z
  .object({
    source_id: z.string(), filename: z.string(), status: z.string(), stage: nn(z.string()), progress_percent: nn(z.number()),
    warnings: z.array(warningSchema), errors: z.array(apiErrorSchema), retryable: nn(z.boolean()),
  })
  .passthrough();
export const batchSchema: Schema<Batch> = z
  .object({
    batch_id: z.string(), name: nn(z.string()), case_id: nn(z.string()), created_at: nn(z.string()), job_id: nn(z.string()),
    sources: z.array(batchSource),
  })
  .passthrough();

export const createBatch = (input: BatchCreateInput) =>
  apiRequest<BatchCreateOutput>(ENDPOINTS.batches, {
    method: "POST", json: input,
    schema: z.object({ batch_id: z.string(), job_id: nn(z.string()) }).passthrough(),
  });
export const getBatch = (id: string, signal?: AbortSignal) =>
  apiRequest<Batch>(`${ENDPOINTS.batches}/${encodeURIComponent(id)}`, { schema: batchSchema, signal });
export const listBatches = (signal?: AbortSignal) =>
  apiRequest<Batch[]>(ENDPOINTS.batches, { schema: z.array(batchSchema), signal });
export const retryBatchSource = (batchId: string, sourceId: string) =>
  apiRequest<unknown>(`${ENDPOINTS.batches}/${encodeURIComponent(batchId)}/sources/${encodeURIComponent(sourceId)}/retry`, { method: "POST" });

export const getSource = (id: string, signal?: AbortSignal) =>
  apiRequest<SourceDocument>(`${ENDPOINTS.sources}/${encodeURIComponent(id)}`, { schema: sourceDocumentSchema, signal });
export const getSourcePage = (id: string, n: number, signal?: AbortSignal) =>
  apiRequest<PageUnit>(`${ENDPOINTS.sources}/${encodeURIComponent(id)}/pages/${n}`, { schema: pageUnitSchema, signal });
export const getSourceMarkdown = (id: string, signal?: AbortSignal) =>
  apiRequest<MarkdownOutput>(`${ENDPOINTS.sources}/${encodeURIComponent(id)}/markdown`, {
    schema: z.object({ markdown: z.string() }).passthrough(), signal,
  });

const caseSummary = z
  .object({ case_id: z.string(), name: z.string(), status: nn(z.string()), created_at: nn(z.string()) })
  .passthrough();
export const listCases = (signal?: AbortSignal) =>
  apiRequest<CaseSummary[]>(ENDPOINTS.cases, { schema: z.array(caseSummary), signal });
export const createCase = (input: CaseCreateInput) =>
  apiRequest<CaseSummary>(ENDPOINTS.cases, { method: "POST", json: input, schema: caseSummary });

export const metricsSchema: Schema<MetricsOutput> = z
  .object({
    items: z.array(z.object({
      id: z.string(), label: z.string(), value: z.number(), unit: nn(z.string()),
      series: nn(z.array(z.object({ t: z.string(), v: z.number() }).passthrough())),
    }).passthrough()),
  })
  .passthrough();
export const getMetrics = (signal?: AbortSignal) => apiRequest<MetricsOutput>(ENDPOINTS.metrics, { schema: metricsSchema, signal });

export const healthSchema: Schema<HealthOutput> = z
  .object({
    agents: z.array(z.object({ id: z.string(), name: z.string(), status: z.string(), latency_ms: nn(z.number()) }).passthrough()),
  })
  .passthrough();
export const getHealth = (signal?: AbortSignal) => apiRequest<HealthOutput>(ENDPOINTS.health, { schema: healthSchema, signal });

export { blockSchema };
