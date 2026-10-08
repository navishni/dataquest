import { useState } from "react";
import { Link } from "react-router-dom";
import { Lock } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ConfidenceBadge } from "@/components/common/ConfidenceBadge";
import { Banner, Dialog, Field } from "@/components/common/Primitives";
import { InlineError } from "@/components/common/StateViews";
import { useToast } from "@/components/common/Toast";
import { EvidenceCrop } from "@/components/viewer/EvidenceCrop";
import { EvidenceChip, EvidenceList } from "@/components/viewer/EvidenceLink";
import { reviewFinding } from "@/api/cases";
import { useCan, useConfig } from "@/hooks/useApp";
import { CAP } from "@/config/capabilityKeys";
import { displayValue, formatNumber } from "@/lib/format";
import type { Comparison, Finding, NotComparable } from "@/agents/16_crossDocReasoning";
import type { Fact } from "@/agents/15_factNormalizer";
import type { EvidenceReference } from "@/types/canonical";

export function FactsTable({ facts }: { facts: Fact[] }) {
  if (facts.length === 0) return <p className="text-sm text-muted">No facts have been returned for this case.</p>;
  return (
    <div className="overflow-auto">
      <table className="min-w-full text-sm">
        <thead><tr><th className="th">Subject</th><th className="th">Metric</th><th className="th">Raw</th><th className="th">Normalized</th><th className="th">Rule</th><th className="th">Confidence</th><th className="th">Evidence</th></tr></thead>
        <tbody>
          {facts.map((f) => (
            <tr key={f.fact_id} className="border-t border-border">
              <td className="td">{isLocked(f, "subject") ? <LockedValue /> : f.subject}</td>
              <td className="td">{f.metric}{f.category && <div className="text-xs text-muted">{f.category}{f.basis ? ` · ${f.basis}` : ""}</div>}</td>
              <td className="td">{isLocked(f, "raw_value") ? <LockedValue /> : <><div>{f.raw_value}</div>{isLocked(f, "raw_text") ? <LockedValue /> : <div className="max-w-xs truncate text-xs text-muted" title={f.raw_text}>{f.raw_text}</div>}</>}</td>
              <td className="td">
                {isLocked(f, "normalized_value") ? <LockedValue /> : f.normalized_value === null ? <span className="text-muted">Not normalized</span>
                  : typeof f.normalized_value === "number" ? formatNumber(f.normalized_value, { currency: f.currency, unit: f.unit }) : f.normalized_value}
                {f.frequency && <div className="text-xs text-muted">{f.frequency}</div>}
                {(f.period_start || f.period_end) && <div className="text-xs text-muted">{displayValue(f.period_start)} - {displayValue(f.period_end)}</div>}
                {f.ambiguity_notes?.map((n, i) => <div key={i} className="text-xs text-warn">Information requiring clarification: {n}</div>)}
              </td>
              <td className="td">{isLocked(f, "normalization_rule") ? <LockedValue /> : <code className="text-xs">{f.normalization_rule}</code>}</td>
              <td className="td">{isLocked(f, "confidence") ? <LockedValue /> : <ConfidenceBadge value={f.confidence} showLabel={false} />}</td>
              <td className="td">{isLocked(f, "evidence") ? <LockedValue /> : <div className="flex flex-col gap-1">{f.evidence.slice(0, 2).map((e, i) => <EvidenceChip key={i} e={e} />)}</div>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function isLocked(f: Fact, column: string) { return f.locked_columns?.includes(column) ?? false; }
function LockedValue() { return <span className="inline-flex items-center gap-1 text-xs text-muted"><Lock size={12} aria-hidden />Hidden</span>; }

export function ComparisonsList({ comparisons, notComparable, facts }: { comparisons: Comparison[]; notComparable: NotComparable[]; facts: Fact[] }) {
  const label = (id: string) => { const f = facts.find((x) => x.fact_id === id); return f ? `${f.metric}: ${f.raw_value}` : id; };
  if (comparisons.length === 0 && notComparable.length === 0) return <p className="text-sm text-muted">No comparisons have been returned.</p>;
  return (
    <div className="space-y-3">
      {comparisons.map((c) => (
        <div key={c.comparison_id} className="card card-pad">
          <div className="flex flex-wrap items-center gap-2 text-sm"><span className="chip">{c.comparable ? "Comparable" : "Not comparable"}</span>{c.rule_id && <code className="text-xs">{c.rule_id}</code>}</div>
          <p className="mt-1 text-sm">{c.fact_ids.map(label).join("  ↔  ")}</p>
          <ul className="mt-2 flex flex-wrap gap-1">{c.checks.map((k, i) => <li key={i} className="chip" title={k.detail}>{k.name}: {k.status}</li>)}</ul>
        </div>
      ))}
      {notComparable.map((n, i) => (
        <div key={i} className="card card-pad border-dashed">
          <span className="chip">Not comparable</span>
          <p className="mt-1 text-sm">{n.fact_ids.map(label).join("  ↔  ")}</p>
          <p className="mt-1 text-sm text-muted">{n.reason}</p>
        </div>
      ))}
    </div>
  );
}

export function FindingCard({
  f, caseId, onRequestDraft, onCompare,
}: { f: Finding; caseId: string; onRequestDraft?: (f: Finding) => void; onCompare?: (e: EvidenceReference[]) => void }) {
  const can = useCan();
  const { severity_levels } = useConfig();
  const toast = useToast();
  const qc = useQueryClient();
  const [dismissOpen, setDismissOpen] = useState(false);
  const [note, setNote] = useState("");
  const review = useMutation({
    mutationFn: (v: { decision: "acknowledge" | "dismiss"; note?: string }) => reviewFinding(caseId, f.finding_id, v),
    onSuccess: (r) => { toast.push("info", `Finding status: ${r.status}`); setDismissOpen(false); void qc.invalidateQueries({ queryKey: ["case", caseId] }); },
  });
  const sev = severity_levels.find((s) => s.id === f.severity)?.label ?? f.severity;
  const canReview = can(CAP.analysis);
  return (
    <article className="card card-pad space-y-3" aria-label={f.title}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-semibold">{f.title}</h3>
          <div className="mt-1 flex flex-wrap gap-1"><span className="chip">Severity: {sev}</span><span className="chip">{f.category}</span><span className="chip">Status: {f.status}</span></div>
        </div>
        <ConfidenceBadge value={f.confidence} />
      </div>
      <Banner>Potential discrepancy · Manual review recommended · No final decision has been made</Banner>
      <p className="text-sm">{f.statement}</p>
      {(f.difference_absolute !== undefined || f.difference_percentage !== undefined) && (
        <p className="text-sm text-muted">
          {f.difference_absolute !== undefined && <>Difference: {formatNumber(f.difference_absolute)}</>}
          {f.difference_percentage !== undefined && <> ({formatNumber(f.difference_percentage)}%)</>}
        </p>
      )}
      {f.possible_explanations.length > 0 && (
        <div><h4 className="label">Possible explanations</h4><ul className="list-disc pl-5 text-sm">{f.possible_explanations.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
      )}
      <p className="text-sm"><span className="text-muted">Recommended review action: </span>{f.recommended_review_action}</p>
      <div><h4 className="label">Evidence</h4><EvidenceList items={f.evidence_references} />
        {f.evidence_references.length >= 2 && onCompare && <button className="btn btn-sm mt-2" onClick={() => onCompare(f.evidence_references)}>Compare side by side</button>}
      </div>
      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
        {canReview && f.status === "open" && <button className="btn btn-sm" disabled={review.isPending} onClick={() => review.mutate({ decision: "acknowledge" })}>Acknowledge</button>}
        {canReview && f.status === "open" && <button className="btn btn-sm" onClick={() => setDismissOpen(true)}>Dismiss with note</button>}
        {can(CAP.draftAction) && onRequestDraft && <button className="btn btn-sm btn-primary" onClick={() => onRequestDraft(f)}>Request action draft</button>}
        {review.isError && <InlineError error={review.error} />}
      </div>
      <Dialog open={dismissOpen} title="Dismiss finding" onClose={() => setDismissOpen(false)}>
        <Field label="Note (required for the record)"><textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <div className="mt-3 flex justify-end gap-2">
          <button className="btn" onClick={() => setDismissOpen(false)}>Cancel</button>
          <button className="btn btn-primary" disabled={!note.trim() || review.isPending} onClick={() => review.mutate({ decision: "dismiss", note: note.trim() })}>Dismiss</button>
        </div>
      </Dialog>
    </article>
  );
}

/** Two evidence references next to each other, each shown on its source page. */
export function EvidenceCompare({ items }: { items: EvidenceReference[] }) {
  const [a, b] = items;
  if (!a || !b) return null;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {[a, b].map((e, i) => (
        <div key={i} className="card card-pad space-y-2">
          <div className="flex items-center justify-between text-sm"><Link className="font-medium text-accent hover:underline" to={`/sources/${encodeURIComponent(e.source_id)}?page=${e.page_number}&block=${encodeURIComponent(e.block_id)}`}>{e.filename} · p. {e.page_number}</Link><ConfidenceBadge value={e.confidence} showLabel={false} /></div>
          <EvidenceCrop e={e} />
          <p className="break-words text-sm">{e.text_excerpt}</p>
        </div>
      ))}
    </div>
  );
}
