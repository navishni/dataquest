/**
 * Agent 02 - Format Router.
 * Purpose: picks the processing route for a source and classifies each page/unit.
 * Input:   { source_id }
 * Output:  { source_id, route, units: [{ unit_id, page_number, page_class }] }
 * Endpoint: POST /agents/format-router
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import type { Schema } from "@/types/schemas";
import type { ID } from "@/types/canonical";

export const ENDPOINT = "/agents/format-router";
export interface Input { source_id: ID }
export interface Output { source_id: ID; route: string; units: { unit_id: ID; page_number: number; page_class: string }[] }
export const outputSchema: Schema<Output> = z
  .object({
    source_id: z.string(), route: z.string(),
    units: z.array(z.object({ unit_id: z.string(), page_number: z.number(), page_class: z.string() }).passthrough()),
  })
  .passthrough();
export const routeFormat = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
