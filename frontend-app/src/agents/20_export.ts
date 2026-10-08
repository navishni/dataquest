/**
 * Agent 20 - Export.
 * Purpose: creates an export of a source, case or table in a chosen format; returns signed download links.
 * Input:   { scope: { type, ids }, format, options?: { masked?, include_evidence? } }
 * Output:  { export_id, format, download_url, content_hash, signed_manifest_url?, created_at }
 * Endpoints: POST /agents/export, GET /agents/export/history -> { exports: Output[] }
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { nn, type Schema } from "@/types/schemas";
import type { ID, ISODate } from "@/types/canonical";

export const ENDPOINT = "/agents/export";
export const ENDPOINT_HISTORY = "/agents/export/history";
export interface Input { scope: { type: string; ids: ID[] }; format: string; options?: { masked?: boolean; include_evidence?: boolean } }
export interface Output {
  export_id: ID; format: string; download_url: string; content_hash: string; signed_manifest_url?: string; created_at: ISODate;
}
export const outputSchema: Schema<Output> = z
  .object({
    export_id: z.string(), format: z.string(), download_url: z.string(), content_hash: z.string(),
    signed_manifest_url: nn(z.string()), created_at: z.string(),
  })
  .passthrough();
export const exportData = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });

export interface HistoryOutput { exports: Output[] }
const historySchema: Schema<HistoryOutput> = z.object({ exports: z.array(outputSchema) }).passthrough();
export const exportHistory = (signal?: AbortSignal) => apiRequest<HistoryOutput>(ENDPOINT_HISTORY, { schema: historySchema, signal });
