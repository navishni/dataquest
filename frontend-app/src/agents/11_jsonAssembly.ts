/**
 * Agent 11 - JSON Assembly.
 * Purpose: assembles all stored extraction results of a source into one evidence-linked document.
 * Input:   { source_id }
 * Output:  SourceDocument
 * Endpoint: POST /agents/json-assembly
 */
import { apiRequest } from "@/api/client";
import { sourceDocumentSchema } from "@/types/schemas";
import type { ID, SourceDocument } from "@/types/canonical";

export const ENDPOINT = "/agents/json-assembly";
export interface Input { source_id: ID }
export type Output = SourceDocument;
export const outputSchema = sourceDocumentSchema;
export const assembleJson = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
