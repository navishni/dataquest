import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { a19 } from "@/agents";
import { DataGrid, type GridCol } from "@/components/common/DataGrid";
import { EmptyState, ErrorState, Skeleton } from "@/components/common/StateViews";
import { formatDateTime } from "@/lib/format";

type Filters = Omit<a19.Input, "cursor">;

/** Tamper-evident audit trail. chain_valid is reported by the backend; the UI never recomputes hashes. */
export function AuditTable({ fixed = {}, showFilters = false }: { fixed?: Filters; showFilters?: boolean }) {
  const [f, setF] = useState<Filters>({});
  const filters: Filters = { ...f, ...Object.fromEntries(Object.entries(fixed).filter(([, v]) => v)) };
  const q = useInfiniteQuery({
    queryKey: ["audit", filters], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => a19.readAudit({ ...filters, cursor: pageParam }, signal),
    getNextPageParam: (last) => last.next_cursor,
  });
  const set = (k: keyof Filters) => (e: React.ChangeEvent<HTMLInputElement>) => setF((o) => ({ ...o, [k]: e.target.value || undefined }));
  const events = q.data?.pages.flatMap((p) => p.events) ?? [];
  const valid = q.data?.pages.every((p) => p.chain_valid);
  const cols: GridCol<a19.AuditEvent>[] = [
    { id: "t", header: "Time", width: "12rem", cell: (e) => formatDateTime(e.timestamp) },
    { id: "a", header: "Actor", width: "11rem", cell: (e) => `${e.actor_id} (${e.actor_role})` },
    { id: "ev", header: "Event", width: "11rem", cell: (e) => e.event_type },
    { id: "o", header: "Object", width: "12rem", cell: (e) => `${e.object_type} ${e.object_id}` },
    { id: "d", header: "Details", width: "16rem", cell: (e) => JSON.stringify(e.details) },
    { id: "h", header: "Hash", width: "10rem", cell: (e) => <code className="text-xs" title={e.hash}>{e.hash.slice(0, 12)}</code> },
    { id: "p", header: "Prev hash", width: "10rem", cell: (e) => <code className="text-xs" title={e.prev_hash}>{e.prev_hash.slice(0, 12)}</code> },
  ];
  return (
    <div className="space-y-3">
      {showFilters && (
        <div className="card card-pad grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {(["from", "to", "actor", "event_type", "case_id", "object_id"] as const).map((k) => (
            <label key={k} className="text-xs text-muted">{k.replace("_", " ")}
              <input className="input mt-1 w-full" type={k === "from" || k === "to" ? "datetime-local" : "text"} onChange={set(k)} /></label>))}
        </div>
      )}
      {q.isPending && <Skeleton lines={5} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <>
          <p className="text-sm" role="status">{valid
            ? <span className="inline-flex items-center gap-1 text-ok"><ShieldCheck size={14} /> Hash chain reported valid by the backend</span>
            : <span className="inline-flex items-center gap-1 text-warn"><ShieldAlert size={14} /> The backend reports the hash chain needs review</span>}</p>
          {events.length === 0 ? <EmptyState title="No audit events matched" /> : <DataGrid cols={cols} rows={events} />}
          {q.hasNextPage && <button className="btn" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>Load more</button>}
        </>
      )}
    </div>
  );
}
