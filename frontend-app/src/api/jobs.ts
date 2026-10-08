import { z } from "zod";
import { apiRequest, apiRequestRaw } from "./client";
import { apiErrorSchema, nn, type Schema } from "@/types/schemas";
import type { JobStatus } from "@/types/api";

export const jobStatusSchema: Schema<JobStatus> = z
  .object({
    job_id: z.string(), status: z.string(), stage: nn(z.string()), progress_percent: nn(z.number()),
    result: z.unknown().optional(), error: nn(apiErrorSchema),
  })
  .passthrough();

export const ENDPOINT_JOBS = "/jobs";
export const getJob = (jobId: string, signal?: AbortSignal) =>
  apiRequest<JobStatus>(`${ENDPOINT_JOBS}/${encodeURIComponent(jobId)}`, { schema: jobStatusSchema, signal });

/** A job is finished when the backend attached a result or an error. The UI never interprets status strings. */
export function jobFinished(j: JobStatus | undefined): boolean {
  return j !== undefined && (j.result !== undefined || j.error !== undefined);
}

export { apiRequestRaw };
