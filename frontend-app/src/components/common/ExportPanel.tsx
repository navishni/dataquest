import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { a20 } from "@/agents";
import { downloadFromUrl } from "@/api/client";
import { Field } from "@/components/common/Primitives";
import { EmptyState, InlineError, QueryBoundary } from "@/components/common/StateViews";
import { useToast } from "@/components/common/Toast";
import { useConfig } from "@/hooks/useApp";
import { formatDateTime } from "@/lib/format";

/** Scope and format lists come from GET /config. Hashes and links come from the backend. */
export function ExportPanel({ defaultScopeIds = [] }: { defaultScopeIds?: string[] }) {
  const cfg = useConfig();
  const toast = useToast();
  const qc = useQueryClient();
  const [scope, setScope] = useState("");
  const [format, setFormat] = useState("");
  const [ids, setIds] = useState(defaultScopeIds.join(", "));
  const [masked, setMasked] = useState(true);
  const [evidence, setEvidence] = useState(false);
  const history = useQuery({ queryKey: ["export-history"], queryFn: ({ signal }) => a20.exportHistory(signal), retry: false });
  const run = useMutation({
    mutationFn: () => a20.exportData({
      scope: { type: scope || cfg.export_scopes[0]?.id || "", ids: ids.split(",").map((s) => s.trim()).filter(Boolean) },
      format: format || cfg.output_formats[0]?.id || "", options: { masked, include_evidence: evidence },
    }),
    onSuccess: () => { toast.push("info", "Export created"); void qc.invalidateQueries({ queryKey: ["export-history"] }); },
  });
  const dl = (url: string, name: string) => downloadFromUrl(url, name).catch((e) => toast.push("error", e instanceof Error ? e.message : "Download failed"));
  const noOptions = cfg.export_scopes.length === 0 || cfg.output_formats.length === 0;
  return (
    <div className="space-y-6">
      <form className="card card-pad grid gap-3 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
        {noOptions && <p className="md:col-span-2 text-sm text-muted">The backend did not list export scopes or formats in /config.</p>}
        <Field label="Scope"><select className="input w-full" value={scope || cfg.export_scopes[0]?.id || ""} onChange={(e) => setScope(e.target.value)}>
          {cfg.export_scopes.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</select></Field>
        <Field label="Format"><select className="input w-full" value={format || cfg.output_formats[0]?.id || ""} onChange={(e) => setFormat(e.target.value)}>
          {cfg.output_formats.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</select></Field>
        <div className="md:col-span-2"><Field label="IDs (comma separated)"><input className="input w-full" value={ids} onChange={(e) => setIds(e.target.value)} /></Field></div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={masked} onChange={(e) => setMasked(e.target.checked)} /> Masked export</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={evidence} onChange={(e) => setEvidence(e.target.checked)} /> Include evidence references</label>
        <div className="md:col-span-2 flex items-center gap-3">
          <button className="btn btn-primary" disabled={run.isPending || noOptions || !ids.trim()}>{run.isPending ? "Creating…" : "Create export"}</button>
          {run.isError && <InlineError error={run.error} />}
        </div>
      </form>
      <section aria-label="Export history">
        <h2 className="mb-2 font-semibold">History</h2>
        <QueryBoundary query={history} isEmpty={(d) => d.exports.length === 0} empty={<EmptyState title="No exports yet" />}>
          {(d) => (
            <ul className="space-y-2">{d.exports.map((x) => (
              <li key={x.export_id} className="card card-pad flex flex-wrap items-center justify-between gap-2 text-sm">
                <div><div className="font-medium">{x.export_id} <span className="chip ml-1">{x.format}</span></div>
                  <div className="text-xs text-muted">{formatDateTime(x.created_at)} · hash <code className="break-all">{x.content_hash}</code></div></div>
                <div className="flex gap-2">
                  <button className="btn btn-sm" onClick={() => void dl(x.download_url, `${x.export_id}.${x.format}`)}><Download size={14} /> Download</button>
                  {x.signed_manifest_url && <button className="btn btn-sm" onClick={() => void dl(x.signed_manifest_url!, `${x.export_id}.manifest`)}>Signed manifest</button>}
                </div></li>))}</ul>
          )}
        </QueryBoundary>
      </section>
    </div>
  );
}
