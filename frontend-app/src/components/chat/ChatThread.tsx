import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Send, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { a24 } from "@/agents";
import { DataGrid, type GridCol } from "@/components/common/DataGrid";
import { InlineError } from "@/components/common/StateViews";
import { useToast } from "@/components/common/Toast";
import { EvidenceChip } from "@/components/viewer/EvidenceLink";
import { CAP } from "@/config/capabilityKeys";
import { useCan } from "@/hooks/useApp";
import { displayValue } from "@/lib/format";

interface Turn { id: number; question: string; answer?: a24.Output; error?: unknown; pending: boolean }

function DecisionBadge({ d }: { d: a24.Output["decision"] }) {
  const Icon = d === "ALLOW" ? ShieldCheck : d === "BLOCK" ? ShieldAlert : ShieldQuestion;
  return <span className="chip font-semibold"><Icon size={13} aria-hidden /> {d}</span>;
}

function ResultTable({ out }: { out: a24.Output }) {
  if (!out.columns || !out.rows) return null;
  const cols: GridCol<unknown[]>[] = out.columns.map((c, i) => ({ id: `${c.name}-${i}`, header: c.name, locked: c.locked, cell: (r) => displayValue(r[i]) }));
  return <DataGrid cols={cols} rows={out.rows} maxHeight={320} />;
}

export function ChatThread({ scope }: { scope?: a24.Input["scope"] }) {
  const can = useCan();
  const toast = useToast();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const convo = useRef<string | undefined>(undefined);
  const nextId = useRef(1);

  const ask = useMutation({
    mutationFn: ({ q }: { q: string; id: number }) => a24.askChat({ question: q, conversation_id: convo.current, scope }),
    onSuccess: (out, v) => { convo.current = out.conversation_id; setTurns((t) => t.map((x) => (x.id === v.id ? { ...x, answer: out, pending: false } : x))); },
    onError: (e, v) => setTurns((t) => t.map((x) => (x.id === v.id ? { ...x, error: e, pending: false } : x))),
  });
  const decide = useMutation({
    mutationFn: (v: a24.ApproveInput) => a24.decideChatApproval(v),
    onSuccess: (out) => toast.push("info", `Decision: ${out.decision}${out.reason ? ` - ${out.reason}` : ""}`),
  });

  const submit = () => {
    const q = text.trim();
    if (!q || ask.isPending) return;
    const id = nextId.current++;
    setTurns((t) => [...t, { id, question: q, pending: true }]);
    setText("");
    ask.mutate({ q, id });
  };

  return (
    <div className="flex h-full min-h-[60vh] flex-col">
      <div className="flex-1 space-y-5 overflow-auto pb-4" aria-live="polite">
        {turns.length === 0 && <p className="py-10 text-center text-sm text-muted">Ask a question about the data you are allowed to see. Every query is checked before it runs.</p>}
        {turns.map((t) => (
          <div key={t.id} className="space-y-2">
            <div className="ml-auto max-w-[80%] rounded-lg bg-accent px-3 py-2 text-sm text-accent-fg">{t.question}</div>
            <div className="card card-pad max-w-full space-y-3">
              {t.pending && <p className="text-sm text-muted">Working on it...</p>}
              {t.error !== undefined && <InlineError error={t.error} />}
              {t.answer && (
                <>
                  <div className="flex flex-wrap items-center gap-2"><DecisionBadge d={t.answer.decision} />{t.answer.reason && <span className="text-sm text-muted">{t.answer.reason}</span>}</div>
                  {t.answer.error && <p role="alert" className="text-sm text-danger"><span className="chip mr-1">{t.answer.error.code}</span>{t.answer.error.message}</p>}
                  {t.answer.sql && <pre className="overflow-auto rounded bg-surface2 p-2 text-xs"><code>{t.answer.sql}</code></pre>}
                  {t.answer.analysis && (
                    <div className="flex flex-wrap gap-1 text-xs">
                      <span className="chip">Operation: {t.answer.analysis.operation}</span>
                      {t.answer.analysis.tables.map((x) => <span key={x} className="chip">Table: {x}</span>)}
                      {t.answer.analysis.columns.map((x) => <span key={x} className="chip">Column: {x}</span>)}
                      {t.answer.analysis.where && <span className="chip">Where: {t.answer.analysis.where}</span>}
                      {t.answer.analysis.estimated_rows !== undefined && <span className="chip">Estimated rows: {t.answer.analysis.estimated_rows}</span>}
                    </div>
                  )}
                  {t.answer.decision === "CONFIRM" && (
                    <div className="rounded border border-warn/40 p-2 text-sm">
                      <p>This query needs approval before it runs. Approval request: <code>{t.answer.approval_id ?? "pending"}</code></p>
                      {can(CAP.admin) && t.answer.approval_id && (
                        <div className="mt-2 flex gap-2">
                          <button className="btn btn-sm" disabled={decide.isPending} onClick={() => decide.mutate({ approval_id: t.answer!.approval_id!, decision: "approve" })}>Approve query</button>
                          <button className="btn btn-sm btn-danger" disabled={decide.isPending} onClick={() => decide.mutate({ approval_id: t.answer!.approval_id!, decision: "reject" })}>Decline query</button>
                        </div>
                      )}
                      {decide.isError && <InlineError error={decide.error} />}
                    </div>
                  )}
                  <ResultTable out={t.answer} />
                  {t.answer.answer_text && <p className="text-sm">{t.answer.answer_text}</p>}
                  {t.answer.citations && t.answer.citations.length > 0 && (
                    <div><h4 className="label">Citations</h4><ul className="grid gap-2 sm:grid-cols-2">{t.answer.citations.map((c, i) => <li key={i}><EvidenceChip e={c} /></li>)}</ul></div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
      </div>
      <form className="flex gap-2 border-t border-border pt-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="sr-only" htmlFor="chat-input">Question</label>
        <input id="chat-input" className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask a question about your data" />
        <button className="btn btn-primary" disabled={!text.trim() || ask.isPending} aria-label="Send question"><Send size={14} /> Ask</button>
      </form>
    </div>
  );
}
