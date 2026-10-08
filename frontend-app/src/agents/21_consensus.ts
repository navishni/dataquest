/**
 * Agent 21 - Consensus (parser jury) and coverage audit.
 * Purpose: shows how several extractors agreed on each block and which page regions no block covers.
 * Input:   { source_id, page_number?, block_id? }
 * Output:  { blocks: [{ block_id, winner, agreement, candidates, escalated, needs_review }],
 *            coverage: [{ page_number, coverage_score, uncovered_regions }] }
 * Endpoint: POST /agents/consensus
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { alternativeSchema, boundingBoxSchema, type Schema } from "@/types/schemas";
import type { Alternative, BoundingBox, ID } from "@/types/canonical";

export const ENDPOINT = "/agents/consensus";
export interface Input { source_id: ID; page_number?: number; block_id?: ID }
export interface Output {
  blocks: { block_id: ID; winner: Alternative; agreement: string; candidates: Alternative[]; escalated: boolean; needs_review: boolean }[];
  coverage: { page_number: number; coverage_score: number; uncovered_regions: BoundingBox[] }[];
}
export const outputSchema: Schema<Output> = z
  .object({
    blocks: z.array(z.object({
      block_id: z.string(), winner: alternativeSchema, agreement: z.string(), candidates: z.array(alternativeSchema),
      escalated: z.boolean(), needs_review: z.boolean(),
    }).passthrough()),
    coverage: z.array(z.object({
      page_number: z.number(), coverage_score: z.number(), uncovered_regions: z.array(boundingBoxSchema),
    }).passthrough()),
  })
  .passthrough();
export const runConsensus = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
