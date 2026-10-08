/**
 * Agent 15 - Fact Normalizer.
 * Purpose: extracts comparable facts (subject, metric, value, period) from a case's documents, each tied to evidence.
 * Input:   { case_id }
 * Output:  { facts: [{ fact_id, subject, metric, raw_text, raw_value, normalized_value, currency?, unit?, frequency?,
 *                      period_start?, period_end?, category?, basis?, normalization_rule, confidence, ambiguity_notes?, evidence }] }
 * Endpoint: POST /agents/fact-normalizer
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { evidenceReferenceSchema, nn, type Schema } from "@/types/schemas";
import type { EvidenceReference, ID, ISODate } from "@/types/canonical";

export const ENDPOINT = "/agents/fact-normalizer";
export interface Input { case_id: ID }
export interface Fact {
  fact_id: ID; subject: string; metric: string; raw_text: string; raw_value: string; normalized_value: number | string | null;
  currency?: string; unit?: string; frequency?: string; period_start?: ISODate; period_end?: ISODate; category?: string; basis?: string;
  normalization_rule: string; confidence: number; ambiguity_notes?: string[]; evidence: EvidenceReference[];
  locked_columns?: string[];
}
export interface Output { facts: Fact[] }
export const factSchema: Schema<Fact> = z
  .object({
    fact_id: z.string(), subject: z.string(), metric: z.string(), raw_text: z.string(), raw_value: z.string(),
    normalized_value: z.union([z.number(), z.string()]).nullable(), currency: nn(z.string()), unit: nn(z.string()),
    frequency: nn(z.string()), period_start: nn(z.string()), period_end: nn(z.string()), category: nn(z.string()), basis: nn(z.string()),
    normalization_rule: z.string(), confidence: z.number(), ambiguity_notes: nn(z.array(z.string())),
    evidence: z.array(evidenceReferenceSchema),
  })
  .passthrough();
export const outputSchema: Schema<Output> = z.object({ facts: z.array(factSchema) }).passthrough();
export const normalizeFacts = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
