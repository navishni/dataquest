/**
 * Agent 04 - OCR.
 * Purpose: recognises text in a page or region using a named engine.
 * Input:   { source_id, page_number, region?: BoundingBox, engine? }
 * Output:  { engine, lines: [{ text, location, confidence }], handwriting_detected? }
 * Endpoint: POST /agents/ocr
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { boundingBoxSchema, nn, type Schema } from "@/types/schemas";
import type { BoundingBox, ID } from "@/types/canonical";

export const ENDPOINT = "/agents/ocr";
export interface Input { source_id: ID; page_number: number; region?: BoundingBox; engine?: string }
export interface Output { engine: string; lines: { text: string; location: BoundingBox; confidence: number }[]; handwriting_detected?: boolean }
export const outputSchema: Schema<Output> = z
  .object({
    engine: z.string(),
    lines: z.array(z.object({ text: z.string(), location: boundingBoxSchema, confidence: z.number() }).passthrough()),
    handwriting_detected: nn(z.boolean()),
  })
  .passthrough();
export const runOcr = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
