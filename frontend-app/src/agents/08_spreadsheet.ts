/**
 * Agent 08 - Spreadsheet.
 * Purpose: reads workbook sheets, cells, formulas, merged ranges and probable tables.
 * Input:   { source_id }
 * Output:  { sheets: [{ name, hidden, used_range, merged_ranges, probable_tables, cells: [...] }] }
 * Endpoint: POST /agents/spreadsheet
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { asSchema, nn, type Schema } from "@/types/schemas";
import type { ID } from "@/types/canonical";

export const ENDPOINT = "/agents/spreadsheet";
export interface Input { source_id: ID }
export interface SheetCell { ref: string; raw_value: unknown; displayed_value: string; formula?: string; number_format?: string; hidden?: boolean }
export interface Output {
  sheets: { name: string; hidden: boolean; used_range: string; merged_ranges: string[]; probable_tables: string[]; cells: SheetCell[] }[];
}
export const outputSchema: Schema<Output> = asSchema<Output>(z
  .object({
    sheets: z.array(z.object({
      name: z.string(), hidden: z.boolean(), used_range: z.string(), merged_ranges: z.array(z.string()),
      probable_tables: z.array(z.string()),
      cells: z.array(z.object({
        ref: z.string(), raw_value: z.unknown(), displayed_value: z.string(), formula: nn(z.string()),
        number_format: nn(z.string()), hidden: nn(z.boolean()),
      }).passthrough()),
    }).passthrough()),
  })
  .passthrough());
export const readSpreadsheet = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
