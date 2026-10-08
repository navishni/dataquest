/**
 * Agent 16 - Cross-Document Reasoning.
 * Purpose: compares facts across documents and reports "potential discrepancy" findings for manual review.
 * Input:   { case_id }
 * Output:  { comparisons, not_comparable, findings } (see types below)
 * Endpoint: POST /agents/cross-doc-reasoning
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { evidenceReferenceSchema, nn, type Schema } from "@/types/schemas";
import type { EvidenceReference, ID } from "@/types/canonical";

export const ENDPOINT = "/agents/cross-doc-reasoning";
export interface Input { case_id: ID }
export interface Check { name: string; status: string; detail?: string }
export interface Comparison { comparison_id: ID; fact_ids: ID[]; comparable: boolean; checks: Check[]; rule_id?: string }
export interface NotComparable { fact_ids: ID[]; reason: string }
export interface Finding {
  finding_id: ID; case_id: ID; category: string; severity: string; status: string; title: string; statement: string;
  difference_absolute?: number; difference_percentage?: number; confidence: number; human_review_required: boolean;
  possible_explanations: string[]; recommended_review_action: string; evidence_references: EvidenceReference[];
}
export interface Output { comparisons: Comparison[]; not_comparable: NotComparable[]; findings: Finding[] }
const check = z.object({ name: z.string(), status: z.string(), detail: nn(z.string()) }).passthrough();
export const findingSchema: Schema<Finding> = z
  .object({
    finding_id: z.string(), case_id: z.string(), category: z.string(), severity: z.string(), status: z.string(), title: z.string(),
    statement: z.string(), difference_absolute: nn(z.number()), difference_percentage: nn(z.number()), confidence: z.number(),
    human_review_required: z.boolean(), possible_explanations: z.array(z.string()), recommended_review_action: z.string(),
    evidence_references: z.array(evidenceReferenceSchema),
  })
  .passthrough();
export const comparisonSchema: Schema<Comparison> = z
  .object({ comparison_id: z.string(), fact_ids: z.array(z.string()), comparable: z.boolean(), checks: z.array(check), rule_id: nn(z.string()) })
  .passthrough();
export const notComparableSchema: Schema<NotComparable> = z.object({ fact_ids: z.array(z.string()), reason: z.string() }).passthrough();
export const outputSchema: Schema<Output> = z
  .object({ comparisons: z.array(comparisonSchema), not_comparable: z.array(notComparableSchema), findings: z.array(findingSchema) })
  .passthrough();
export const reasonAcrossDocs = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
