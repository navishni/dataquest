/**
 * Agent 17 - Action Draft.
 * Purpose: drafts a follow-up action (never sent) for a finding; a person must approve it.
 * Input:   { case_id, finding_id?, action_type }
 * Output:  ProposedAction
 * Endpoint: POST /agents/action-draft
 * Optional extension: `allowed_decisions` (string[]) on ProposedAction lets the backend say which decisions are valid now.
 */
import { z } from "zod";
import { apiRequest } from "@/api/client";
import { evidenceReferenceSchema, nn, type Schema } from "@/types/schemas";
import type { EvidenceReference, ID, ISODate } from "@/types/canonical";

export const ENDPOINT = "/agents/action-draft";
export interface Input { case_id: ID; finding_id?: ID; action_type: string }
export interface PolicyCheck { name: string; status: string; detail?: string }
export interface ProposedAction {
  action_id: ID; case_id: ID; finding_id?: ID; action_type: string; status: string; risk_level: string;
  requires_human_approval: boolean; generated_by: string; created_at: ISODate; updated_at: ISODate;
  draft_payload: { subject?: string; recipients?: string[]; body: string; attachments?: string[] };
  supporting_evidence: EvidenceReference[]; policy_checks: PolicyCheck[];
  approved_by?: string; approved_at?: ISODate; rejected_by?: string; rejected_at?: ISODate; rejection_reason?: string;
  executed_at?: ISODate; execution_result?: unknown; idempotency_key: string; final_content_hash?: string; labels: string[];
  allowed_decisions?: string[];
}
export type Output = ProposedAction;
export const proposedActionSchema: Schema<ProposedAction> = z
  .object({
    action_id: z.string(), case_id: z.string(), finding_id: nn(z.string()), action_type: z.string(), status: z.string(),
    risk_level: z.string(), requires_human_approval: z.boolean(), generated_by: z.string(), created_at: z.string(),
    updated_at: z.string(),
    draft_payload: z.object({
      subject: nn(z.string()), recipients: nn(z.array(z.string())), body: z.string(), attachments: nn(z.array(z.string())),
    }).passthrough(),
    supporting_evidence: z.array(evidenceReferenceSchema),
    policy_checks: z.array(z.object({ name: z.string(), status: z.string(), detail: nn(z.string()) }).passthrough()),
    approved_by: nn(z.string()), approved_at: nn(z.string()), rejected_by: nn(z.string()), rejected_at: nn(z.string()),
    rejection_reason: nn(z.string()), executed_at: nn(z.string()), execution_result: z.unknown().optional(),
    idempotency_key: z.string(), final_content_hash: nn(z.string()), labels: z.array(z.string()),
    allowed_decisions: nn(z.array(z.string())),
  })
  .passthrough();
export const outputSchema = proposedActionSchema;
export const draftAction = (input: Input) => apiRequest<Output>(ENDPOINT, { method: "POST", json: input, schema: outputSchema });
