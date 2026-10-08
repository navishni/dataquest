/**
 * Agent 01 - File Validation (gatekeeper).
 * Purpose: validates an uploaded file (type, size, integrity, password) and registers it as a source.
 * Input:   multipart form: `file` (binary) and optional `password`.
 * Output:  { source_id, sanitized_filename, sha256, detected_mime, size_bytes, page_count?, status, error?, duplicate_of? }
 * Endpoint: POST /agents/file-validation
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { apiErrorSchema, nn, type Schema } from "@/types/schemas";
import type { ApiError, ID } from "@/types/canonical";

export const ENDPOINT = "/agents/file-validation";
export interface Input { file: File; options?: { password?: string } }
export interface Output {
  source_id?: ID; sanitized_filename: string; sha256: string; detected_mime?: string; size_bytes: number; page_count?: number;
  status: "accepted" | "rejected"; error?: ApiError; duplicate_of?: ID;
}
export const outputSchema: Schema<Output> = z
  .object({
    source_id: nn(z.string()), sanitized_filename: z.string(), sha256: z.string(), detected_mime: nn(z.string()), size_bytes: z.number(),
    page_count: nn(z.number()), status: z.enum(["accepted", "rejected"]), error: nn(apiErrorSchema), duplicate_of: nn(z.string()),
  })
  .passthrough();

export async function validateFile(input: Input): Promise<Output> {
  const form = new FormData();
  form.append("file", input.file, input.file.name);
  if (input.options?.password) form.append("password", input.options.password);
  return apiRequest<Output>(ENDPOINT, { method: "POST", form, schema: outputSchema });
}
