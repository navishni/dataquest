/**
 * Agent 14 - Case Linker.
 * Purpose: links sources to a case explicitly, or suggests links; a person confirms or rejects suggestions.
 * Input:   { case_id, source_ids, mode: "explicit" | "suggest" }
 * Output:  { links: [{ link_id, case_id, source_id, relationship_type, relationship_confidence, matching_signals,
 *                      linked_by, linked_at, human_verified }] }
 * Endpoints: POST /agents/case-linker, POST /agents/case-linker/decision  ({ link_id, decision } -> { link_id, human_verified })
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import type { Schema } from "@/types/schemas";
import type { ID, ISODate } from "@/types/canonical";

export const ENDPOINT = "/agents/case-linker";
export const ENDPOINT_DECISION = "/agents/case-linker/decision";
export interface Input { case_id: ID; source_ids: ID[]; mode: "explicit" | "suggest" }
export interface Link {
  link_id: ID; case_id: ID; source_id: ID; relationship_type: string; relationship_confidence: number; matching_signals: string[];
  linked_by: string; linked_at: ISODate; human_verified: boolean;
}
export interface Output { links: Link[] }
export const linkSchema: Schema<Link> = z
  .object({
    link_id: z.string(), case_id: z.string(), source_id: z.string(), relationship_type: z.string(), relationship_confidence: z.number(),
    matching_signals: z.array(z.string()), linked_by: z.string(), linked_at: z.string(), human_verified: z.boolean(),
  })
  .passthrough();
export const outputSchema: Schema<Output> = z.object({ links: z.array(linkSchema) }).passthrough();
export const linkCase = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });

export interface DecisionInput { link_id: ID; decision: "confirm" | "reject" }
export interface DecisionOutput { link_id: ID; human_verified: boolean }
const decisionSchema: Schema<DecisionOutput> = z.object({ link_id: z.string(), human_verified: z.boolean() }).passthrough();
export const decideLink = (input: DecisionInput) =>
  apiRequest<DecisionOutput>(ENDPOINT_DECISION, { method: "POST", json: input, schema: decisionSchema });
