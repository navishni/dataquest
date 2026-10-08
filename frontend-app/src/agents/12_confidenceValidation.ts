/**
 * Agent 12 - Confidence & Validation.
 * Purpose: reports per-block confidence, validation checks and document-level badges.
 * Input:   { source_id }
 * Output:  { document_confidence, blocks: [{ block_id, confidence, confidence_breakdown, checks, needs_review }], badges }
 * Endpoint: POST /agents/confidence-validation
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { nn, validationCheckSchema, type Schema } from "@/types/schemas";
import type { ID, ValidationCheck } from "@/types/canonical";

export const ENDPOINT = "/agents/confidence-validation";
export interface Input { source_id: ID }
export interface Output {
  document_confidence: number;
  blocks: { block_id: ID; confidence: number; confidence_breakdown: Record<string, number>; checks: ValidationCheck[]; needs_review: boolean }[];
  badges: { scope_id: ID; label: string; status: string; detail?: string }[];
}
export const outputSchema: Schema<Output> = z
  .object({
    document_confidence: z.number(),
    blocks: z.array(z.object({
      block_id: z.string(), confidence: z.number(), confidence_breakdown: z.record(z.number()),
      checks: z.array(validationCheckSchema), needs_review: z.boolean(),
    }).passthrough()),
    badges: z.array(z.object({ scope_id: z.string(), label: z.string(), status: z.string(), detail: nn(z.string()) }).passthrough()),
  })
  .passthrough();
export const validateConfidence = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
