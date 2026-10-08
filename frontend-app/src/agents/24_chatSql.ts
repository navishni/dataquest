/**
 * Agent 24 - Chat (natural language to governed SQL).
 * Purpose: answers a question through a policy-checked read-only query. The backend returns ALLOW, CONFIRM or BLOCK.
 * Input:   { question, conversation_id?, scope?: { resource_ids?, case_id? } }
 * Output:  { conversation_id, sql?, analysis?, decision, reason?, approval_id?, columns?, rows?, answer_text?, citations?, error? }
 * Endpoint: POST /agents/chat-sql
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { apiErrorSchema, evidenceReferenceSchema, nn, type Schema } from "@/types/schemas";
import type { ApiError, EvidenceReference, ID } from "@/types/canonical";

export const ENDPOINT = "/agents/chat-sql";
export interface Input { question: string; conversation_id?: ID; scope?: { resource_ids?: ID[]; case_id?: ID } }
export interface Analysis { operation: string; tables: string[]; columns: string[]; where?: string; estimated_rows?: number }
export interface Output {
  conversation_id: ID; sql?: string; analysis?: Analysis; decision: "ALLOW" | "CONFIRM" | "BLOCK"; reason?: string; approval_id?: ID;
  columns?: { name: string; locked: boolean }[]; rows?: unknown[][]; answer_text?: string; citations?: EvidenceReference[]; error?: ApiError;
}
export const outputSchema: Schema<Output> = z
  .object({
    conversation_id: z.string(), sql: nn(z.string()),
    analysis: nn(z.object({
      operation: z.string(), tables: z.array(z.string()), columns: z.array(z.string()), where: nn(z.string()), estimated_rows: nn(z.number()),
    }).passthrough()),
    decision: z.enum(["ALLOW", "CONFIRM", "BLOCK"]), reason: nn(z.string()), approval_id: nn(z.string()),
    columns: nn(z.array(z.object({ name: z.string(), locked: z.boolean() }).passthrough())),
    rows: nn(z.array(z.array(z.unknown()))), answer_text: nn(z.string()), citations: nn(z.array(evidenceReferenceSchema)),
    error: nn(apiErrorSchema),
  })
  .passthrough();
export const askChat = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });

/**
 * Companion endpoint (not part of the original 24-agent list, documented in docs/CONTRACT.md):
 * POST /agents/chat-sql/approve  { approval_id, decision: "approve" | "reject" } -> same shape as Output.
 * Only offered to users whose capabilities include the admin key.
 */
export const ENDPOINT_APPROVE = "/agents/chat-sql/approve";
export interface ApproveInput { approval_id: ID; decision: "approve" | "reject" }
export const decideChatApproval = (input: ApproveInput) =>
  apiRequest<Output>(ENDPOINT_APPROVE, { method: "POST", json: input, schema: outputSchema });
