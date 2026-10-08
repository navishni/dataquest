/**
 * Agent 23 - Access Control.
 * Purpose: shows which tables/columns are locked for the caller, previews visible data, and handles access requests.
 * Locked columns arrive with `locked: true` and null values. The UI never asks for or shows hidden data.
 * Endpoints:
 *   GET  /agents/access/schema                      -> { tables: [{ resource_id, schema, name, locked, columns: [{ name, type?, locked }] }] }
 *   GET  /agents/access/preview?resource_id=&limit= -> { columns: [{ name, locked }], rows: (unknown | null)[][] }
 *   POST /agents/access/request    { resource_id, columns?, reason, requested_duration? } -> { request_id, status }
 *   GET  /agents/access/requests                    -> { requests: [{ request_id, user_id, resource_id, columns?, reason, status, requested_at, valid_until? }] }
 *   POST /agents/access/decision   { request_id, decision, valid_until?, notes? } -> { request_id, status, signature? }
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { nn, type Schema } from "@/types/schemas";
import type { ID, ISODate } from "@/types/canonical";

export const ENDPOINT_SCHEMA = "/agents/access/schema";
export const ENDPOINT_PREVIEW = "/agents/access/preview";
export const ENDPOINT_REQUEST = "/agents/access/request";
export const ENDPOINT_REQUESTS = "/agents/access/requests";
export const ENDPOINT_DECISION = "/agents/access/decision";
export const ENDPOINT_POLICY = "/agents/access/policy";

export interface SchemaOutput {
  tables: { resource_id: ID; schema: string; name: string; locked: boolean; columns: { name: string; type?: string; locked: boolean; hidden_for_viewers?: boolean }[] }[];
}
export interface PreviewInput { resource_id: ID; limit?: number }
export interface PreviewOutput { columns: { name: string; locked: boolean }[]; rows: (unknown | null)[][] }
export interface RequestInput { resource_id: ID; columns?: string[]; reason: string; requested_duration?: string }
export interface RequestOutput { request_id: ID; status: string }
export interface AccessRequest {
  request_id: ID; user_id: string; resource_id: ID; columns?: string[]; reason: string; status: string; requested_at: ISODate; valid_until?: ISODate;
}
export interface RequestsOutput { requests: AccessRequest[] }
export interface DecisionInput { request_id: ID; decision: "approve" | "reject"; valid_until?: ISODate; notes?: string }
export interface DecisionOutput { request_id: ID; status: string; signature?: string }
export interface ViewerPolicyInput { resource_id: ID; hidden_columns: string[] }
export interface ViewerPolicyOutput { resource: ID; hidden_columns: string[]; updated_by: string; updated_at: ISODate }

const schemaOut: Schema<SchemaOutput> = z
  .object({
    tables: z.array(z.object({
      resource_id: z.string(), schema: z.string(), name: z.string(), locked: z.boolean(),
      columns: z.array(z.object({ name: z.string(), type: nn(z.string()), locked: z.boolean(), hidden_for_viewers: nn(z.boolean()) }).passthrough()),
    }).passthrough()),
  })
  .passthrough();
const previewOut: Schema<PreviewOutput> = z
  .object({
    columns: z.array(z.object({ name: z.string(), locked: z.boolean() }).passthrough()),
    rows: z.array(z.array(z.unknown())),
  })
  .passthrough();
const requestOut: Schema<RequestOutput> = z.object({ request_id: z.string(), status: z.string() }).passthrough();
const requestsOut: Schema<RequestsOutput> = z
  .object({
    requests: z.array(z.object({
      request_id: z.string(), user_id: z.string(), resource_id: z.string(), columns: nn(z.array(z.string())), reason: z.string(),
      status: z.string(), requested_at: z.string(), valid_until: nn(z.string()),
    }).passthrough()),
  })
  .passthrough();
const decisionOut: Schema<DecisionOutput> = z
  .object({ request_id: z.string(), status: z.string(), signature: nn(z.string()) })
  .passthrough();

export const getAccessSchema = (signal?: AbortSignal) => apiRequest<SchemaOutput>(ENDPOINT_SCHEMA, { schema: schemaOut, signal });
export const previewAccess = (input: PreviewInput, signal?: AbortSignal) =>
  apiRequest<PreviewOutput>(ENDPOINT_PREVIEW, { query: { ...input }, schema: previewOut, signal });
export const requestAccess = (input: RequestInput) =>
  apiRequest<RequestOutput>(ENDPOINT_REQUEST, { method: "POST", json: input, schema: requestOut });
export const listAccessRequests = (signal?: AbortSignal) =>
  apiRequest<RequestsOutput>(ENDPOINT_REQUESTS, { schema: requestsOut, signal });
export const decideAccess = (input: DecisionInput) =>
  apiRequest<DecisionOutput>(ENDPOINT_DECISION, { method: "POST", json: input, schema: decisionOut });
const policyOut: Schema<ViewerPolicyOutput> = z.object({
  resource: z.string(), hidden_columns: z.array(z.string()), updated_by: z.string(), updated_at: z.string(),
}).passthrough();
export const updateViewerPolicy = (input: ViewerPolicyInput) =>
  apiRequest<ViewerPolicyOutput>(ENDPOINT_POLICY, { method: "PUT", json: input, schema: policyOut });
