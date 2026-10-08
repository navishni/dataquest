import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, Eye, EyeOff, Lock, ShieldCheck } from "lucide-react";
import { a23 } from "@/agents";
import { DataGrid, type GridCol } from "@/components/common/DataGrid";
import { Dialog, Field } from "@/components/common/Primitives";
import { EmptyState, InlineError, QueryBoundary } from "@/components/common/StateViews";
import { useToast } from "@/components/common/Toast";
import { CAP } from "@/config/capabilityKeys";
import { useCan } from "@/hooks/useApp";
import { displayValue, formatDateTime } from "@/lib/format";

type Table = a23.SchemaOutput["tables"][number];

export function RequestAccessDialog({ table, onClose }: { table: Table | null; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [duration, setDuration] = useState("");
  const [cols, setCols] = useState<string[]>([]);
  const toast = useToast();
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => a23.requestAccess({ resource_id: table!.resource_id, columns: cols.length ? cols : undefined, reason: reason.trim(), requested_duration: duration || undefined }),
    onSuccess: (r) => { toast.push("info", `Access request ${r.request_id}: ${r.status}`); void qc.invalidateQueries({ queryKey: ["access-requests"] }); setReason(""); setCols([]); onClose(); },
  });
  if (!table) return null;
  const lockedCols = table.columns.filter((c) => c.locked);
  return (
    <Dialog open title={`Request access to ${table.name}`} onClose={onClose}>
      <div className="space-y-3">
        {lockedCols.length > 0 && (
          <fieldset><legend className="label">Columns (leave empty to request the whole resource)</legend>
            <div className="flex flex-wrap gap-2">{lockedCols.map((c) => (
              <label key={c.name} className="chip cursor-pointer"><input type="checkbox" className="mr-1" checked={cols.includes(c.name)} onChange={(e) => setCols(e.target.checked ? [...cols, c.name] : cols.filter((x) => x !== c.name))} />{c.name}</label>
            ))}</div></fieldset>
        )}
        <Field label="Reason"><textarea className="input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <Field label="Requested duration (optional; hours or days)"><input className="input" value={duration} onChange={(e) => setDuration(e.target.value)} /></Field>
        {m.isError && <InlineError error={m.error} />}
        <div className="flex justify-end gap-2"><button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={!reason.trim() || m.isPending} onClick={() => m.mutate()}>Send request</button></div>
      </div>
    </Dialog>
  );
}

function SchemaBrowserContent({ tables, selected, onSelect, manage, request, onRequest, policyPending, onPolicyChange }: {
  tables: Table[]; selected: string | null; onSelect: (id: string) => void;
  manage: boolean; request: boolean; onRequest: (table: Table) => void;
  policyPending: boolean; onPolicyChange: (table: Table, column: string, hiddenForViewers: boolean) => void;
}) {
  const active = tables.find((t) => t.resource_id === selected) ?? null;
  useEffect(() => {
    const first = tables[0];
    if (!selected && first) onSelect(first.resource_id);
  }, [onSelect, selected, tables]);

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {tables.map((t) => {
          const lockedCount = t.locked ? t.columns.length : t.columns.filter((c) => c.locked).length;
          const isSelected = selected === t.resource_id;
          return (
            <button key={t.resource_id} type="button" onClick={() => onSelect(t.resource_id)} aria-pressed={isSelected}
              className={`card flex min-h-[88px] items-center justify-between gap-3 p-4 text-left transition-colors hover:bg-surface2 ${isSelected ? "border-accent ring-1 ring-accent" : ""}`}>
              <span className="flex min-w-0 items-center gap-3">
                <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${isSelected ? "bg-accent/10 text-accent" : "bg-surface2 text-muted"}`}><Database size={18} aria-hidden /></span>
                <span className="min-w-0"><span className="block truncate font-semibold">{t.name}</span><span className="mt-1 block text-xs text-muted">{t.columns.length} columns · {t.schema}</span></span>
              </span>
              {lockedCount > 0 && <span className="chip shrink-0"><Lock size={11} aria-hidden />{t.locked ? "Restricted" : `${lockedCount} hidden`}</span>}
            </button>
          );
        })}
      </div>

      {active && (
        <section className="rounded-xl border border-border bg-bg/40 p-4 sm:p-5" aria-label={`${active.name} column settings`}>
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
            <div>
              <div className="flex flex-wrap items-center gap-2"><h3 className="text-base font-semibold">{active.name}</h3><span className="chip">{active.schema}</span>{active.locked && <span className="chip"><Lock size={11} />Restricted</span>}</div>
              <p className="mt-1.5 text-sm text-muted">{manage ? "Turn on Hide from viewers for any column that should require admin approval." : "Hidden columns are shown as locked placeholders until an admin approves your request."}</p>
            </div>
            {request && (active.locked || active.columns.some((c) => c.locked)) && (
              <button className="btn btn-primary shrink-0" onClick={() => onRequest(active)}><ShieldCheck size={15} aria-hidden />Request access</button>
            )}
          </div>

          {active.locked ? (
            <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted"><Lock size={18} className="mx-auto mb-2" aria-hidden />This table is restricted for your account. Request access to ask an admin to review it.</div>
          ) : (
            <ul className="grid gap-2 md:grid-cols-2">
              {active.columns.map((c) => (
                <li key={c.name} className="flex min-w-0 items-center justify-between gap-4 rounded-lg border border-border bg-surface px-3 py-2.5">
                  <div className="flex min-w-0 items-center gap-2.5">
                    {c.locked ? <Lock size={14} className="shrink-0 text-muted" aria-hidden /> : <Database size={14} className="shrink-0 text-muted" aria-hidden />}
                    <span className="min-w-0"><span className="block break-words text-sm font-medium">{c.name}</span><span className="mt-0.5 block text-xs text-muted">{c.locked ? "Hidden from your account" : c.type || "Column"}</span></span>
                  </div>
                  {manage && !c.locked ? (
                    <label className="flex shrink-0 cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-xs text-muted hover:bg-surface2">
                      <input type="checkbox" className="h-4 w-4 accent-accent" aria-label={`Hide ${c.name} from viewers`} checked={c.hidden_for_viewers ?? false}
                        disabled={policyPending} onChange={(e) => onPolicyChange(active, c.name, e.target.checked)} />
                      <span className="flex items-center gap-1.5">{c.hidden_for_viewers ? <EyeOff size={13} aria-hidden /> : <Eye size={13} aria-hidden />}Hide from viewers</span>
                    </label>
                  ) : c.locked ? <span className="chip shrink-0"><Lock size={11} aria-hidden />Hidden</span> : <span className="text-xs text-muted">Visible</span>}
                </li>
              ))}
            </ul>
          )}
          {manage && !active.locked && <p className="mt-3 text-xs text-muted">Visibility changes are saved immediately. Admins and editors can still see these columns.</p>}
        </section>
      )}
    </div>
  );
}

export function SchemaBrowser({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["access-schema"], queryFn: ({ signal }) => a23.getAccessSchema(signal) });
  const [req, setReq] = useState<Table | null>(null);
  const policy = useMutation({
    mutationFn: a23.updateViewerPolicy,
    onSuccess: () => { toast.push("info", "Viewer visibility updated"); void qc.invalidateQueries({ queryKey: ["access-schema"] }); },
  });
  const manage = can(CAP.manageAccess);
  const request = can(CAP.requestAccess);
  return (
    <QueryBoundary query={q} isEmpty={(d) => d.tables.length === 0} empty={<EmptyState title="No tables were returned" />}>
      {(d) => <>
        <SchemaBrowserContent tables={d.tables} selected={selected} onSelect={onSelect} manage={manage} request={request}
          onRequest={setReq} policyPending={policy.isPending}
          onPolicyChange={(table, column, hiddenForViewers) => {
            const hidden = new Set(table.columns.filter((x) => x.hidden_for_viewers).map((x) => x.name));
            if (hiddenForViewers) hidden.add(column); else hidden.delete(column);
            policy.mutate({ resource_id: table.resource_id, hidden_columns: [...hidden].sort() });
          }} />
        <RequestAccessDialog table={req} onClose={() => setReq(null)} />
        {policy.isError && <InlineError error={policy.error} />}
      </>}
    </QueryBoundary>
  );
}

export function PreviewTable({ resourceId }: { resourceId: string }) {
  const q = useQuery({ queryKey: ["access-preview", resourceId], queryFn: ({ signal }) => a23.previewAccess({ resource_id: resourceId }, signal) });
  return (
    <QueryBoundary query={q} isEmpty={(d) => d.columns.length === 0} empty={<EmptyState title="No preview available" />}>
      {(d) => {
        const cols: GridCol<unknown[]>[] = d.columns.map((c, i) => ({ id: c.name, header: c.name, locked: c.locked, cell: (r) => displayValue(r[i]) }));
        return <DataGrid cols={cols} rows={d.rows as unknown[][]} />;
      }}
    </QueryBoundary>
  );
}

export function AdminQueue() {
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["access-requests"], queryFn: ({ signal }) => a23.listAccessRequests(signal) });
  const [deciding, setDeciding] = useState<a23.AccessRequest | null>(null);
  const [until, setUntil] = useState("");
  const [notes, setNotes] = useState("");
  const m = useMutation({
    mutationFn: (v: a23.DecisionInput) => a23.decideAccess(v),
    onSuccess: (r) => { toast.push("info", `Request ${r.request_id}: ${r.status}`); setDeciding(null); void qc.invalidateQueries({ queryKey: ["access-requests"] }); },
  });
  const admin = can(CAP.admin);
  return (
    <QueryBoundary query={q} isEmpty={(d) => d.requests.length === 0} empty={<EmptyState title="No access requests" />}>
      {(d) => (
        <>
          <div className="overflow-auto"><table className="min-w-full text-sm">
            <thead><tr><th className="th">Requested</th><th className="th">User</th><th className="th">Resource</th><th className="th">Columns</th><th className="th">Reason</th><th className="th">Status</th><th className="th">Valid until</th>{admin && <th className="th">Decision</th>}</tr></thead>
            <tbody>{d.requests.map((r) => (
              <tr key={r.request_id} className="border-t border-border">
                <td className="td">{formatDateTime(r.requested_at)}</td><td className="td">{r.user_id}</td><td className="td">{r.resource_id}</td>
                <td className="td">{r.columns?.join(", ")}</td><td className="td max-w-xs break-words">{r.reason}</td>
                <td className="td"><span className="chip">{r.status}</span></td><td className="td">{formatDateTime(r.valid_until)}</td>
          {admin && <td className="td">{r.status === "pending" ? <div className="flex gap-1">
                  <button className="btn btn-sm" onClick={() => { setDeciding(r); setUntil(""); setNotes(""); }}>Approve</button>
                  <button className="btn btn-sm btn-danger" disabled={m.isPending} onClick={() => m.mutate({ request_id: r.request_id, decision: "reject" })}>Reject</button></div> : <span className="text-muted">—</span>}</td>}
              </tr>
            ))}</tbody></table></div>
          {m.isError && <InlineError error={m.error} />}
          <Dialog open={deciding !== null} title="Approve access request" onClose={() => setDeciding(null)}>
            <div className="space-y-3">
              <Field label="Valid until"><input type="datetime-local" className="input" value={until} onChange={(e) => setUntil(e.target.value)} /></Field>
              <Field label="Notes (optional)"><input className="input" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
              <div className="flex justify-end gap-2"><button className="btn" onClick={() => setDeciding(null)}>Cancel</button>
                <button className="btn btn-primary" disabled={!until || m.isPending} onClick={() => deciding && m.mutate({ request_id: deciding.request_id, decision: "approve", valid_until: new Date(until).toISOString(), notes: notes || undefined })}>Approve</button></div>
            </div>
          </Dialog>
        </>
      )}
    </QueryBoundary>
  );
}
