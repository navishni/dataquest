import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { a18, a19 } from "@/agents";
import type { ProposedAction } from "@/agents/17_actionDraft";
import type { ApprovalEvent, Decision } from "@/agents/18_humanApproval";
import { Banner, Dialog, Field, KeyValue } from "@/components/common/Primitives";
import { InlineError, QueryBoundary } from "@/components/common/StateViews";
import { EvidenceList } from "@/components/viewer/EvidenceLink";
import { ApiRequestError } from "@/api/errors";
import { useCan } from "@/hooks/useApp";
import { CAP, DECISION_CAP } from "@/config/capabilityKeys";
import { formatDateTime } from "@/lib/format";
import { useToast } from "@/components/common/Toast";

const DECISION_LABEL: { id: Decision; label: string; tone?: "primary" | "danger" }[] = [
  { id: "submit_review", label: "Submit for review" },
  { id: "approve", label: "Approve", tone: "primary" },
  { id: "reject", label: "Reject", tone: "danger" },
  { id: "execute", label: "Execute", tone: "primary" },
  { id: "cancel", label: "Cancel draft" },
];

export function ActionReview({ action, caseId }: { action: ProposedAction; caseId: string }) {
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(action.draft_payload.subject ?? "");
  const [body, setBody] = useState(action.draft_payload.body);
  const [notes, setNotes] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [sessionEvents, setSessionEvents] = useState<ApprovalEvent[]>([]);
  const [signature, setSignature] = useState<a18.Output["signature"]>();
  const [allowedFromError, setAllowedFromError] = useState<string[] | null>(null);

  const decide = useMutation({
    mutationFn: (v: a18.Input) => a18.decideApproval(v),
    onSuccess: (out) => {
      // Always show the status the backend returns, then refetch the case so every pane agrees.
      setSessionEvents((xs) => [...xs, out.event]);
      if (out.signature) setSignature(out.signature);
      setAllowedFromError(null);
      setEditing(false);
      qc.setQueryData(["case", caseId], (old: { actions: ProposedAction[] } | undefined) =>
        old ? { ...old, actions: old.actions.map((a) => (a.action_id === out.action.action_id ? out.action : a)) } : old);
      void qc.invalidateQueries({ queryKey: ["case", caseId] });
      void qc.invalidateQueries({ queryKey: ["audit-action", action.action_id] });
      toast.push("info", `Status is now: ${out.action.status}`);
    },
    onError: (e) => {
      if (e instanceof ApiRequestError && Array.isArray(e.details?.allowed)) setAllowedFromError(e.details.allowed.filter((x): x is string => typeof x === "string"));
    },
  });

  const allowedByBackend = action.allowed_decisions ?? allowedFromError;
  const enabled = (d: string) => can(DECISION_CAP[d] ?? CAP.admin) && (allowedByBackend ? allowedByBackend.includes(d) : true) && !decide.isPending;
  const p = action.draft_payload;

  const audit = useQuery({
    queryKey: ["audit-action", action.action_id],
    queryFn: ({ signal }) => a19.readAudit({ object_id: action.action_id }, signal),
    enabled: can(CAP.audit),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Banner>AI-generated draft</Banner><Banner>Not sent</Banner><Banner>Requires human approval</Banner><Banner>No final decision has been made</Banner>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="chip font-semibold">Status: {action.status}</span><span className="chip">{action.action_type}</span><span className="chip">Risk: {action.risk_level}</span>
        {action.labels.map((l) => <span key={l} className="chip">{l}</span>)}
      </div>

      <section className="card card-pad space-y-3">
        {editing ? (
          <>
            <Field label="Subject"><input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} /></Field>
            <Field label="Body"><textarea className="input" rows={10} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
            <Field label="Notes (optional)"><input className="input" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
            <div className="flex gap-2">
              <button className="btn btn-primary" disabled={!enabled("save_edit")} onClick={() => decide.mutate({ action_id: action.action_id, decision: "save_edit", edited_fields: { subject, body }, notes: notes || undefined })}>Save edit</button>
              <button className="btn" onClick={() => { setEditing(false); setSubject(p.subject ?? ""); setBody(p.body); }}>Discard changes</button>
            </div>
          </>
        ) : (
          <>
            <KeyValue items={[
              { k: "Subject", v: p.subject ?? "" },
              { k: "Recipients", v: p.recipients?.join(", ") ?? "" },
              { k: "Attachments", v: p.attachments?.join(", ") ?? "" },
            ]} />
            <div><h3 className="label">Body</h3><p className="whitespace-pre-wrap break-words rounded bg-surface2 p-3 text-sm">{p.body}</p></div>
            {enabled("save_edit") && <button className="btn btn-sm" onClick={() => setEditing(true)}>Edit draft</button>}
          </>
        )}
      </section>

      <section className="card card-pad">
        <h3 className="mb-2 font-semibold">Policy checks</h3>
        {action.policy_checks.length === 0 ? <p className="text-sm text-muted">No policy checks were returned.</p> : (
          <ul className="space-y-1 text-sm">{action.policy_checks.map((c, i) => <li key={i}><span className="chip mr-1">{c.status}</span>{c.name}{c.detail && <span className="text-muted"> - {c.detail}</span>}</li>)}</ul>
        )}
      </section>

      <section className="card card-pad">
        <h3 className="mb-2 font-semibold">Supporting evidence</h3>
        <EvidenceList items={action.supporting_evidence} />
      </section>

      <section className="card card-pad space-y-3">
        <h3 className="font-semibold">Review</h3>
        <Field label="Notes for this decision (optional)"><input className="input" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        <div className="flex flex-wrap gap-2">
          {DECISION_LABEL.map((d) => (
            <button
              key={d.id} disabled={!enabled(d.id)} title={!can(DECISION_CAP[d.id] ?? CAP.admin) ? "Your account does not have the capability for this step" : undefined}
              className={`btn ${d.tone === "primary" ? "btn-primary" : d.tone === "danger" ? "btn-danger" : ""}`}
              onClick={() => (d.id === "reject" ? setRejectOpen(true) : decide.mutate({ action_id: action.action_id, decision: d.id, notes: notes || undefined }))}
            >{d.label}</button>
          ))}
        </div>
        {decide.isError && <InlineError error={decide.error} />}
        <KeyValue items={[
          ...(action.approved_by ? [{ k: "Approved by", v: `${action.approved_by} ${formatDateTime(action.approved_at)}` }] : []),
          ...(action.rejected_by ? [{ k: "Rejected by", v: `${action.rejected_by} ${formatDateTime(action.rejected_at)}` }] : []),
          ...(action.rejection_reason ? [{ k: "Reason", v: action.rejection_reason }] : []),
          ...(action.executed_at ? [{ k: "Executed", v: formatDateTime(action.executed_at) }] : []),
          ...(action.final_content_hash ? [{ k: "Content hash", v: <code className="break-all text-xs">{action.final_content_hash}</code> }] : []),
        ]} />
        {signature && (
          <div className="rounded border border-border p-2 text-sm">
            <h4 className="label">Signature</h4>
            <KeyValue items={[
              { k: "Signed by", v: signature.signed_by }, { k: "Algorithm", v: signature.algorithm },
              { k: "Value", v: <code className="break-all text-xs">{signature.value}</code> },
              ...(signature.expires_at ? [{ k: "Expires", v: formatDateTime(signature.expires_at) }] : []),
            ]} />
          </div>
        )}
      </section>

      <section className="card card-pad">
        <h3 className="mb-2 font-semibold">Approval timeline</h3>
        {sessionEvents.length > 0 && (
          <ol className="mb-3 space-y-1 text-sm">{sessionEvents.map((e) => (
            <li key={e.event_id}>{formatDateTime(e.event_timestamp)} · {e.actor_id} ({e.actor_role}) · {e.event_type}: {e.previous_status} → {e.new_status}</li>
          ))}</ol>
        )}
        {can(CAP.audit) ? (
          <QueryBoundary query={audit} isEmpty={(d) => d.events.length === 0} empty={<p className="text-sm text-muted">No audit events for this action yet.</p>}>
            {(d) => <ol className="space-y-1 text-sm">{d.events.map((e) => <li key={e.event_id}>{formatDateTime(e.timestamp)} · {e.actor_id} ({e.actor_role}) · {e.event_type}</li>)}</ol>}
          </QueryBoundary>
        ) : sessionEvents.length === 0 && <p className="text-sm text-muted">Events from this session will appear here.</p>}
      </section>

      <Dialog open={rejectOpen} title="Reject this draft" onClose={() => setRejectOpen(false)}>
        <Field label="Reason (required)"><textarea className="input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <div className="mt-3 flex justify-end gap-2">
          <button className="btn" onClick={() => setRejectOpen(false)}>Cancel</button>
          <button className="btn btn-danger" disabled={!reason.trim() || decide.isPending} onClick={() => { decide.mutate({ action_id: action.action_id, decision: "reject", rejection_reason: reason.trim(), notes: notes || undefined }); setRejectOpen(false); }}>Reject draft</button>
        </div>
      </Dialog>
    </div>
  );
}
