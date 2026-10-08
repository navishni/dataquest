/**
 * Agent 05 - Layout Detection.
 * Purpose: finds typed regions (text, table, figure, ...) on a page.
 * Input:   { source_id, page_number }
 * Output:  { layout_class, regions: [{ region_id, type, location, confidence }] }
 * Endpoint: POST /agents/layout
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { boundingBoxSchema, type Schema } from "@/types/schemas";
import type { BoundingBox, ID } from "@/types/canonical";

export const ENDPOINT = "/agents/layout";
export interface Input { source_id: ID; page_number: number }
export interface Output { layout_class: string; regions: { region_id: ID; type: string; location: BoundingBox; confidence: number }[] }
export const outputSchema: Schema<Output> = z
  .object({
    layout_class: z.string(),
    regions: z.array(z.object({ region_id: z.string(), type: z.string(), location: boundingBoxSchema, confidence: z.number() }).passthrough()),
  })
  .passthrough();
export const detectLayout = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
