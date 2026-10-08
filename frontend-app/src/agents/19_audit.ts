/**
 * Agent 19 - Audit.
 * Purpose: reads the tamper-evident audit trail (hash chained) with filters and cursor paging.
 * Input:   query params { from?, to?, actor?, event_type?, case_id?, object_id?, cursor? }
 * Output:  { events: [{ event_id, timestamp, actor_id, actor_role, event_type, object_type, object_id, details, prev_hash, hash }],
 *            chain_valid, next_cursor? }
 * Endpoint: GET /agents/audit
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { nn, type Schema } from "@/types/schemas";
import type { ID, ISODate } from "@/types/canonical";

export const ENDPOINT = "/agents/audit";
export interface Input { from?: ISODate; to?: ISODate; actor?: string; event_type?: string; case_id?: ID; object_id?: ID; cursor?: string }
export interface AuditEvent {
  event_id: ID; timestamp: ISODate; actor_id: string; actor_role: string; event_type: string; object_type: string; object_id: ID;
  details: Record<string, unknown>; prev_hash: string; hash: string;
}
export interface Output { events: AuditEvent[]; chain_valid: boolean; next_cursor?: string }
export const outputSchema: Schema<Output> = z
  .object({
    events: z.array(z.object({
      event_id: z.string(), timestamp: z.string(), actor_id: z.string(), actor_role: z.string(), event_type: z.string(),
      object_type: z.string(), object_id: z.string(), details: z.record(z.unknown()), prev_hash: z.string(), hash: z.string(),
    }).passthrough()),
    chain_valid: z.boolean(), next_cursor: nn(z.string()),
  })
  .passthrough();
export const readAudit = (input: Input, signal?: AbortSignal) =>
  apiRequest<Output>(ENDPOINT, { query: { ...input }, schema: outputSchema, signal });
