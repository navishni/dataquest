/**
 * Agent 13 - Virtual Merge.
 * Purpose: maps several sources into one virtual document with a global page order (no pixels are merged).
 * Input:   { batch_id, ordered_source_ids }
 * Output:  { virtual_document_id, pages: [{ virtual_page_number, source_id, page_number, page_id, boundary_start }] }
 * Endpoint: POST /agents/virtual-merge
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import type { Schema } from "@/types/schemas";
import type { ID } from "@/types/canonical";

export const ENDPOINT = "/agents/virtual-merge";
export interface Input { batch_id: ID; ordered_source_ids: ID[] }
export interface Output {
  virtual_document_id: ID;
  pages: { virtual_page_number: number; source_id: ID; page_number: number; page_id: ID; boundary_start: boolean }[];
}
export const outputSchema: Schema<Output> = z
  .object({
    virtual_document_id: z.string(),
    pages: z.array(z.object({
      virtual_page_number: z.number(), source_id: z.string(), page_number: z.number(), page_id: z.string(), boundary_start: z.boolean(),
    }).passthrough()),
  })
  .passthrough();
export const mergeVirtual = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
