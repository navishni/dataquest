/**
 * Agent 03 - Native Text Extraction.
 * Purpose: reads the embedded text layer of a page with positions.
 * Input:   { source_id, page_number }
 * Output:  { page_id, spans: [{ text, location, font?, confidence }], has_usable_text }
 * Endpoint: POST /agents/native-text
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { boundingBoxSchema, nn, type Schema } from "@/types/schemas";
import type { BoundingBox, ID } from "@/types/canonical";

export const ENDPOINT = "/agents/native-text";
export interface Input { source_id: ID; page_number: number }
export interface Output { page_id: ID; spans: { text: string; location: BoundingBox; font?: string; confidence: number }[]; has_usable_text: boolean }
export const outputSchema: Schema<Output> = z
  .object({
    page_id: z.string(),
    spans: z.array(z.object({ text: z.string(), location: boundingBoxSchema, font: nn(z.string()), confidence: z.number() }).passthrough()),
    has_usable_text: z.boolean(),
  })
  .passthrough();
export const extractNativeText = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
