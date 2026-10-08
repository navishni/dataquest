/**
 * Agent 22 - URL Guard and Web Render.
 * Purpose: checks a link against the ingestion policy and, if allowed, renders the page as a new source.
 * Input:   { url, purpose?, authorization_basis? }
 * Output:  { status: "allowed" | "blocked", reason?, robots_checked, source_id?, snapshot?, error? }
 * Endpoint: POST /agents/url-ingest
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { apiErrorSchema, nn, type Schema } from "@/types/schemas";
import type { ApiError, ID, ISODate } from "@/types/canonical";

export const ENDPOINT = "/agents/url-ingest";
export interface Input { url: string; purpose?: string; authorization_basis?: string }
export interface Output {
  status: "allowed" | "blocked"; reason?: string; robots_checked: boolean; source_id?: ID;
  snapshot?: { screenshot_url: string; fetched_at: ISODate }; error?: ApiError;
}
export const outputSchema: Schema<Output> = z
  .object({
    status: z.enum(["allowed", "blocked"]), reason: nn(z.string()), robots_checked: z.boolean(), source_id: nn(z.string()),
    snapshot: nn(z.object({ screenshot_url: z.string(), fetched_at: z.string() }).passthrough()), error: nn(apiErrorSchema),
  })
  .passthrough();
export const ingestUrl = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
