import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { a14, a15, a16, a17 } from "@/agents";
import { getCase } from "@/api/cases";
import { listCases } from "@/api/platform";
import { ConfidenceBadge } from "@/components/common/ConfidenceBadge";
import { KeyValue, PageHeader } from "@/components/common/Primitives";
import { EmptyState, InlineError, QueryBoundary } from "@/components/common/StateViews";
import { useToast } from "@/components/common/Toast";
import { ComparisonsList, EvidenceCompare, FactsTable, FindingCard } from "@/components/findings/FindingsPanels";
import { CAP } from "@/config/capabilityKeys";
import { useCan, useConfig } from "@/hooks/useApp";
import { formatDateTime } from "@/lib/format";
import type { EvidenceReference } from "@/types/canonical";

export function CasesPage() {
  const q = useQuery({ queryKey: ["cases"], queryFn: ({ signal }) => listCases(signal) });
  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Cases" subtitle="Cases group related documents for review. No final decision is made here." />
      <QueryBoundary query={q} isEmpty={(d) => d.length === 0} empty={<EmptyState title="No cases yet" hint="Create a case from the Upload page." />}>
        {(cases) => <ul className="space-y-2">{cases.map((c) => (
          <li key={c.case_id}><Link className="card flex items-center justify-between p-3 hover:bg-surface2" to={`/cases/${encodeURIComponent(c.case_id)}`}>
            <span className="font-medium">{c.name}</span><span className="text-xs text-muted">{c.status && <span className="chip mr-2">{c.status}</span>}{formatDateTime(c.created_at)}</span></Link></li>))}</ul>}
      </QueryBoundary>
    </div>
  );
}

export function CaseReviewPage() {
  const { caseId = "" } = useParams();
  const can = useCan();
  const cfg = useConfig();
  const toast = useToast();
  const qc = useQueryClient();
  const [compare, setCompare] = useState<EvidenceReference[]>([]);
  const q = useQuery({ queryKey: ["case", caseId], queryFn: ({ signal }) => getCase(caseId, signal) });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["case", caseId] });
  const run = useMutation({
    mutationFn: async (which: "facts" | "reason" | "suggest") => {
      if (which === "facts") return a15.normalizeFacts({ case_id: caseId });
      if (which === "reason") return a16.reasonAcrossDocs({ case_id: caseId });
      return a14.linkCase({ case_id: caseId, source_ids: q.data?.sources.map((s) => s.source_id) ?? [], mode: "suggest" });
    },
    onSuccess: () => { toast.push("info", "Analysis updated"); refresh(); },
  });
  const decide = useMutation({
    mutationFn: (v: { link_id: string; decision: "confirm" | "reject" }) => a14.decideLink(v),
    onSuccess: refresh,
  });
  const draft = useMutation({
    mutationFn: (v: { finding_id?: string; action_type: string }) => a17.draftAction({ case_id: caseId, ...v }),
    onSuccess: () => { toast.push("info", "Action draft created"); refresh(); },
  });
  const [actionType, setActionType] = useState("");

  return (
    <div>
      <PageHeader title="Case review" subtitle="Potential discrepancies and information requiring clarification. Manual review recommended; no final decision has been made."
        actions={can(CAP.analysis) && <>
          <button className="btn" disabled={run.isPending} onClick={() => run.mutate("suggest")}>Suggest links</button>
          <button className="btn" disabled={run.isPending} onClick={() => run.mutate("facts")}>Normalize facts</button>
          <button className="btn btn-primary" disabled={run.isPending} onClick={() => run.mutate("reason")}>Compare facts</button></>} />
      {run.isError && <InlineError error={run.error} />}
      <QueryBoundary query={q}>
        {(c) => (
          <div className="grid gap-4 lg:grid-cols-[300px,1fr,380px]">
            <aside className="space-y-4" aria-label="Case information">
              <div className="card card-pad"><KeyValue items={[{ k: "Case", v: c.name }, { k: "Status", v: c.status ?? "" }, { k: "Created", v: formatDateTime(c.created_at) }]} /></div>
              <section className="card card-pad"><h2 className="mb-2 font-semibold">Linked documents</h2>
                {c.sources.length === 0 ? <p className="text-sm text-muted">No documents are linked.</p> :
                  <ul className="space-y-1 text-sm">{c.sources.map((s) => <li key={s.source_id}><Link className="text-accent hover:underline" to={`/sources/${encodeURIComponent(s.source_id)}`}>{s.filename}</Link></li>)}</ul>}</section>
              <section className="card card-pad"><h2 className="mb-2 font-semibold">Suggested links</h2>
                {c.links.length === 0 ? <p className="text-sm text-muted">No links were returned.</p> :
                  <ul className="space-y-2 text-sm">{c.links.map((l) => (
                    <li key={l.link_id} className="space-y-1"><div className="flex justify-between"><span>{c.sources.find((s) => s.source_id === l.source_id)?.filename ?? l.source_id}</span><ConfidenceBadge value={l.relationship_confidence} showLabel={false} /></div>
                      <div className="text-xs text-muted">{l.relationship_type} · {l.matching_signals.join(", ")}</div>
                      {l.human_verified ? <span className="chip">Confirmed by a person</span> : can(CAP.analysis) && (
                        <div className="flex gap-1"><button className="btn btn-sm" onClick={() => decide.mutate({ link_id: l.link_id, decision: "confirm" })}>Confirm</button>
                          <button className="btn btn-sm" onClick={() => decide.mutate({ link_id: l.link_id, decision: "reject" })}>Reject link</button></div>)}</li>))}</ul>}
                {decide.isError && <InlineError error={decide.error} />}</section>
              <section className="card card-pad"><h2 className="mb-2 font-semibold">Timeline</h2>
                {c.timeline.length === 0 ? <p className="text-sm text-muted">No timeline entries.</p> :
                  <ol className="space-y-1 text-sm">{c.timeline.map((t, i) => <li key={i}><span className="text-xs text-muted">{formatDateTime(t.timestamp)}</span><br />{t.label}{t.actor && <span className="text-muted"> · {t.actor}</span>}</li>)}</ol>}</section>
            </aside>
            <section className="space-y-4" aria-label="Evidence preview">
              {compare.length >= 2 ? <EvidenceCompare items={compare} /> : <div className="card card-pad text-sm text-muted">Choose “Compare evidence” on a finding to see the sources side by side.</div>}
              <FactsTable facts={c.facts} />
            </section>
            <section className="space-y-4" aria-label="Findings">
              <ComparisonsList comparisons={c.comparisons} notComparable={c.not_comparable} facts={c.facts} />
              <h2 className="font-semibold">Findings</h2>
              {c.findings.length === 0 && <EmptyState title="No findings were returned" />}
              {c.findings.map((f) => <FindingCard key={f.finding_id} f={f} caseId={caseId} onCompare={setCompare}
                onRequestDraft={(x) => draft.mutate({ finding_id: x.finding_id, action_type: actionType || cfg.action_types[0]?.id || "" })} />)}
              {can(CAP.draftAction) && cfg.action_types.length > 0 && (
                <div className="card card-pad flex flex-wrap items-center gap-2 text-sm"><label>Action type
                  <select className="input ml-2" value={actionType || cfg.action_types[0]?.id} onChange={(e) => setActionType(e.target.value)}>{cfg.action_types.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</select></label>
                  <button className="btn" disabled={draft.isPending} onClick={() => draft.mutate({ action_type: actionType || cfg.action_types[0]!.id })}>Draft an action</button></div>)}
              {draft.isError && <InlineError error={draft.error} />}
              {c.actions.length > 0 && <div><h2 className="mb-2 font-semibold">Action drafts</h2><ul className="space-y-2">{c.actions.map((a) => (
                <li key={a.action_id}><Link className="card flex justify-between p-3 text-sm hover:bg-surface2" to={`/cases/${encodeURIComponent(caseId)}/actions/${encodeURIComponent(a.action_id)}`}><span>{a.draft_payload.subject ?? a.action_type}</span><span className="chip">{a.status}</span></Link></li>))}</ul></div>}
            </section>
          </div>
        )}
      </QueryBoundary>
    </div>
  );
}
