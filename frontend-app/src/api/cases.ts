// Case endpoints (GET /cases/{id}) and the finding review call. Additional endpoints are listed in docs/CONTRACT.md.
import { z } from "zod";
import { apiRequest } from "./client";
import { nn, type Schema } from "@/types/schemas";
import { linkSchema, type Link } from "@/agents/14_caseLinker";
import { factSchema, type Fact } from "@/agents/15_factNormalizer";
import {
  comparisonSchema, findingSchema, notComparableSchema, type Comparison, type Finding, type NotComparable,
} from "@/agents/16_crossDocReasoning";
import { proposedActionSchema, type ProposedAction } from "@/agents/17_actionDraft";
import type { ID, ISODate } from "@/types/canonical";

export interface CaseDetail {
  case_id: ID; name: string; status?: string; created_at?: ISODate;
  sources: { source_id: ID; filename: string }[];
  links: Link[]; facts: Fact[]; comparisons: Comparison[]; not_comparable: NotComparable[]; findings: Finding[]; actions: ProposedAction[];
  timeline: { timestamp: ISODate; label: string; actor?: string }[];
}

export const caseDetailSchema: Schema<CaseDetail> = z
  .object({
    case_id: z.string(), name: z.string(), status: nn(z.string()), created_at: nn(z.string()),
    sources: z.array(z.object({ source_id: z.string(), filename: z.string() }).passthrough()).default([]),
    links: z.array(linkSchema).default([]),
    facts: z.array(factSchema).default([]),
    comparisons: z.array(comparisonSchema).default([]),
    not_comparable: z.array(notComparableSchema).default([]),
    findings: z.array(findingSchema).default([]),
    actions: z.array(proposedActionSchema).default([]),
    timeline: z.array(z.object({ timestamp: z.string(), label: z.string(), actor: nn(z.string()) }).passthrough()).default([]),
  })
  .passthrough();

export const getCase = (id: string, signal?: AbortSignal) =>
  apiRequest<CaseDetail>(`/cases/${encodeURIComponent(id)}`, { schema: caseDetailSchema, signal });

export interface FindingReviewInput { decision: "acknowledge" | "dismiss"; note?: string }
export interface FindingReviewOutput { finding_id: ID; status: string }
export const reviewFinding = (caseId: string, findingId: string, input: FindingReviewInput) =>
  apiRequest<FindingReviewOutput>(`/cases/${encodeURIComponent(caseId)}/findings/${encodeURIComponent(findingId)}/review`, {
    method: "POST", json: input,
    schema: z.object({ finding_id: z.string(), status: z.string() }).passthrough(),
  });
