/**
 * Agent 07 - Table Extraction.
 * Purpose: turns a table region into cells with spans, headers and per-cell confidence.
 * Input:   { source_id, page_number, region_id }
 * Output:  TableBlock
 * Endpoint: POST /agents/table
 */
import { apiRequest } from "@/api/client";
import { tableBlockSchema } from "@/types/schemas";
import type { ID, TableBlock } from "@/types/canonical";

export const ENDPOINT = "/agents/table";
export interface Input { source_id: ID; page_number: number; region_id: ID }
export type Output = TableBlock;
export const outputSchema = tableBlockSchema;
export const extractTable = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
