/**
 * Agent 18 - Human Approval.
 * Purpose: moves a drafted action through review. The backend alone decides whether a transition is valid.
 * Input:   { action_id, decision, notes?, edited_fields?, rejection_reason? }
 * Output:  { action: ProposedAction, event: {...}, signature?: {...} }
 * Endpoint: POST /agents/human-approval
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { nn, type Schema } from "@/types/schemas";
import { proposedActionSchema, type ProposedAction } from "./17_actionDraft";
import type { ID, ISODate } from "@/types/canonical";

export const ENDPOINT = "/agents/human-approval";
export type Decision = "save_edit" | "submit_review" | "approve" | "reject" | "cancel" | "execute";
export interface Input {
  action_id: ID; decision: Decision; notes?: string; edited_fields?: Record<string, unknown>; rejection_reason?: string;
}
export interface ApprovalEvent {
  event_id: ID; actor_id: string; actor_role: string; event_type: string; event_timestamp: ISODate; previous_status: string;
  new_status: string; notes?: string; edited_fields?: string[]; content_hash?: string;
}
export interface Output {
  action: ProposedAction; event: ApprovalEvent;
  signature?: { signed_by: string; algorithm: string; value: string; expires_at?: ISODate };
}
export const eventSchema: Schema<ApprovalEvent> = z
  .object({
    event_id: z.string(), actor_id: z.string(), actor_role: z.string(), event_type: z.string(), event_timestamp: z.string(),
    previous_status: z.string(), new_status: z.string(), notes: nn(z.string()), edited_fields: nn(z.array(z.string())),
    content_hash: nn(z.string()),
  })
  .passthrough();
export const outputSchema: Schema<Output> = z
  .object({
    action: proposedActionSchema, event: eventSchema,
    signature: nn(z.object({ signed_by: z.string(), algorithm: z.string(), value: z.string(), expires_at: nn(z.string()) }).passthrough()),
  })
  .passthrough();
export const decideApproval = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
