/**
 * Agent 06 - Reading Order.
 * Purpose: orders page regions the way a person would read them.
 * Input:   { page_id, region_ids }
 * Output:  { ordered_ids, reading_order_confidence, warnings }
 * Endpoint: POST /agents/reading-order
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { warningSchema, type Schema } from "@/types/schemas";
import type { ID, WarningItem } from "@/types/canonical";

export const ENDPOINT = "/agents/reading-order";
export interface Input { page_id: ID; region_ids: ID[] }
export interface Output { ordered_ids: ID[]; reading_order_confidence: number; warnings: WarningItem[] }
export const outputSchema: Schema<Output> = z
  .object({ ordered_ids: z.array(z.string()), reading_order_confidence: z.number(), warnings: z.array(warningSchema) })
  .passthrough();
export const orderReading = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
