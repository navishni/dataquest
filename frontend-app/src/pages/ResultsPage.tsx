import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { a13 } from "@/agents";
import { getBatch, getSourceMarkdown } from "@/api/platform";
import { getCase } from "@/api/cases";
import { ConfidenceBadge } from "@/components/common/ConfidenceBadge";
import { JsonTree } from "@/components/common/JsonTree";
import { LineChart } from "@/components/common/LineChart";
import { Markdown } from "@/components/common/Markdown";
import { AuthedImage } from "@/components/common/AuthedImage";
import { KeyValue, PageHeader, Tabs } from "@/components/common/Primitives";
import { EmptyState, ErrorState, QueryBoundary, Skeleton } from "@/components/common/StateViews";
import { ExportPanel } from "@/components/common/ExportPanel";
import { AuditTable } from "@/components/common/AuditTable";
import { TableGrid } from "@/components/tables/TableGrid";
import { DocumentMap } from "@/components/viewer/DocumentMap";
import { ComparisonsList, FactsTable, FindingCard } from "@/components/findings/FindingsPanels";
import { CAP } from "@/config/capabilityKeys";
import { useSourceBundle } from "@/hooks/useSourceBundle";
import { useCan } from "@/hooks/useApp";
import { formatBytes } from "@/lib/format";
import { isChartBlock, isEquationBlock, isFigureBlock, isTableBlock } from "@/types/canonical";

type TabId = "overview" | "sources" | "json" | "markdown" | "tables" | "figures" | "equations" | "warnings" | "map" | "virtual" | "combined" | "case" | "actions" | "exports" | "audit";

export function ResultsPage() {
  const { batchId = "" } = useParams();
  const can = useCan();
  const nav = useNavigate();
  const [tab, setTab] = useState<TabId>("overview");
  const [sourceId, setSourceId] = useState<string | undefined>(undefined);
  const [visualPreview, setVisualPreview] = useState<{ src: string; alt: string } | null>(null);

  const batch = useQuery({ queryKey: ["batch", batchId], queryFn: ({ signal }) => getBatch(batchId, signal) });
  const activeId = sourceId ?? batch.data?.sources[0]?.source_id;
  const bundle = useSourceBundle(activeId);
  const md = useQuery({ queryKey: ["markdown", activeId], queryFn: ({ signal }) => getSourceMarkdown(activeId!, signal), enabled: !!activeId, retry: false });
  const orderedIds = batch.data?.sources.map((s) => s.source_id) ?? [];
  const virtual = useQuery({
    queryKey: ["virtual", batchId, orderedIds], queryFn: () => a13.mergeVirtual({ batch_id: batchId, ordered_source_ids: orderedIds }),
    enabled: orderedIds.length > 0, retry: false,
  });
  const caseQ = useQuery({ queryKey: ["case", batch.data?.case_id], queryFn: ({ signal }) => getCase(batch.data!.case_id!, signal), enabled: !!batch.data?.case_id, retry: false });

  const blocks = useMemo(() => bundle.pages.flatMap((p) => p.blocks), [bundle.pages]);
  const tables = blocks.filter(isTableBlock);
  const figures = blocks.filter((b) => isChartBlock(b) || isFigureBlock(b));
  const equations = blocks.filter(isEquationBlock);
  const warnings = [...(bundle.doc?.warnings ?? []), ...blocks.flatMap((b) => b.warnings)];
  const errors = bundle.doc?.errors ?? [];

  const tabs: { id: TabId; label: string }[] = [
    { id: "overview", label: "Overview" }, { id: "sources", label: "Source documents" }, { id: "json", label: "Structured JSON" },
    ...(md.data ? [{ id: "markdown" as const, label: "Markdown" }] : []),
    ...(tables.length ? [{ id: "tables" as const, label: "Tables" }] : []),
    ...(figures.length ? [{ id: "figures" as const, label: "Figures and charts" }] : []),
    ...(equations.length ? [{ id: "equations" as const, label: "Equations" }] : []),
    { id: "warnings", label: "Warnings and errors" },
    ...(bundle.pages.length ? [{ id: "map" as const, label: "Document map" }] : []),
    ...(virtual.data ? [{ id: "virtual" as const, label: "Virtual document" }] : []),
    ...(caseQ.data ? [{ id: "combined" as const, label: "Combined analysis" }, { id: "case" as const, label: "Case review" }, { id: "actions" as const, label: "Action drafts" }] : []),
    ...(can(CAP.export) ? [{ id: "exports" as const, label: "Exports" }] : []),
    ...(can(CAP.audit) ? [{ id: "audit" as const, label: "Audit log" }] : []),
  ];
  const current = tabs.some((t) => t.id === tab) ? tab : "overview";

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Results" subtitle={batch.data?.name ?? batchId}
        actions={<>{batch.data && batch.data.sources.length > 1 && (
          <select className="input" aria-label="Source document" value={activeId ?? ""} onChange={(e) => setSourceId(e.target.value)}>
            {batch.data.sources.map((s) => <option key={s.source_id} value={s.source_id}>{s.filename}</option>)}</select>)}
          {activeId && <Link className="btn" to={`/sources/${encodeURIComponent(activeId)}`}>Open viewer</Link>}</>} />
      <QueryBoundary query={batch} isEmpty={(d) => d.sources.length === 0} empty={<EmptyState title="This batch has no sources" />}>
        {(b) => (
          <>
            <Tabs tabs={tabs} value={current} onChange={setTab} />
            <div className="mt-4">
              {bundle.pending && <Skeleton lines={5} />}
              {!!bundle.error && <ErrorState error={bundle.error} onRetry={bundle.refetch} />}
              {current === "overview" && bundle.doc && (
                <div className="card card-pad"><KeyValue items={[
                  { k: "File", v: bundle.doc.filename }, { k: "Kind", v: bundle.doc.kind }, { k: "Status", v: bundle.doc.status },
                  { k: "Pages", v: bundle.doc.page_count }, { k: "Size", v: formatBytes(bundle.doc.size_bytes) },
                  { k: "SHA-256", v: <code className="break-all text-xs">{bundle.doc.sha256}</code> },
                  { k: "Origin", v: bundle.doc.origin.type + (bundle.doc.origin.url ? ` · ${bundle.doc.origin.url}` : "") },
                  ...(bundle.doc.document_confidence !== undefined ? [{ k: "Document confidence", v: <ConfidenceBadge value={bundle.doc.document_confidence} /> }] : []),
                  { k: "Warnings", v: warnings.length }, { k: "Errors", v: errors.length },
                ]} /></div>
              )}
              {current === "sources" && (
                <ul className="space-y-2">{b.sources.map((s) => (
                  <li key={s.source_id} className="card flex items-center justify-between p-3 text-sm"><span>{s.filename} <span className="chip ml-2">{s.status}</span></span>
                    <span className="flex gap-2"><button className="btn btn-sm" onClick={() => { setSourceId(s.source_id); setTab("overview"); }}>Show</button><Link className="btn btn-sm" to={`/sources/${encodeURIComponent(s.source_id)}`}>Viewer</Link></span></li>))}</ul>
              )}
              {current === "json" && bundle.doc && <JsonTree value={{ ...bundle.doc, pages: bundle.pages }} />}
              {current === "markdown" && md.data && <div className="card card-pad"><Markdown source={md.data.markdown} /></div>}
              {current === "tables" && (
                <div className="space-y-6">{tables.map((t) => (
                  <div key={t.block_id}><TableGrid table={t} onSelectCell={(c) => {
                    const tablePage = bundle.pages.find((p) => p.page_id === t.page_id)?.page_number ?? 1;
                    const targetPage = c.source_page_number ?? tablePage;
                    if (targetPage !== tablePage) nav(`/sources/${encodeURIComponent(t.source_id)}?page=${targetPage}`);
                    else nav(`/sources/${encodeURIComponent(t.source_id)}?page=${targetPage}&block=${encodeURIComponent(t.block_id)}&cell=${c.row}-${c.col}`);
                  }}
                    onOpenLinked={(id) => { const o = tables.find((x) => x.block_id === id); if (o) document.getElementById(`tbl-${id}`)?.scrollIntoView(); }} />
                    <div id={`tbl-${t.block_id}`} /></div>))}</div>
              )}
              {current === "figures" && (
                <div className="grid gap-4 md:grid-cols-2">{figures.map((f) => {
                  const chart = isChartBlock(f) ? f : null;
                  const figure = isFigureBlock(f) ? f : null;
                  const imageUrl = chart?.crop_url ?? figure?.crop_url;
                  if (!imageUrl) return null;
                  const title = chart?.title || figure?.caption || (chart ? "Chart preview" : "Figure preview");
                  const pageNumber = bundle.pages.find((p) => p.page_id === f.page_id)?.page_number;
                  const chartType = (chart?.chart_type ?? "").toLowerCase();
                  const chartKind = /bar|column|histogram/.test(chartType) ? "bar" : "line";
                  return (
                    <article key={f.block_id} className="card overflow-hidden">
                      <div className="flex items-start justify-between gap-3 p-3">
                        <div className="min-w-0"><h3 className="truncate font-medium">{title}</h3>
                          <p className="text-xs text-muted">{chart ? "Chart" : "Figure"}{pageNumber ? ` · page ${pageNumber}` : ""}</p></div>
                        <ConfidenceBadge value={f.confidence} />
                      </div>
                      <button type="button" className="group relative block w-full bg-surface2 text-left" aria-label={`Open full-size preview: ${title}`}
                        onClick={() => setVisualPreview({ src: imageUrl, alt: title })}>
                        <AuthedImage src={imageUrl} alt={title} className="max-h-80 w-full object-contain" />
                        <span className="absolute bottom-2 right-2 rounded bg-black/70 px-2 py-1 text-xs text-white opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100">Open full-size preview</span>
                      </button>
                      {chart && <div className="space-y-3 p-3">
                        {chart.series?.length ? chart.series.map((s) => (
                          <div key={s.name} className="rounded border border-border p-2">
                            <div className="mb-1 text-xs font-medium">{s.name}</div>
                            <LineChart points={s.points} label={s.name} kind={chartKind} />
                          </div>
                        )) : <p className="text-sm text-muted">Chart values were not extracted; the source image is shown above.</p>}
                        {chart.insight_text && <p className="text-sm">{chart.insight_text}</p>}
                        {chart.warnings?.map((w, i) => <p key={i} className="text-xs text-warn"><span className="chip mr-1">{w.code}</span>{w.message}</p>)}
                      </div>}
                    </article>
                  );
                })}</div>
              )}
              {current === "equations" && (
                <div className="space-y-3">{equations.map((e) => isEquationBlock(e) && (
                  <div key={e.block_id} className="card card-pad space-y-2"><AuthedImage src={e.crop_url} alt="Equation" className="max-h-24" />
                    <p className="break-words font-mono text-sm">{e.latex ?? e.plain_text ?? ""}</p><span className="chip">{e.verified ? "Verified" : "Not verified"}</span> <ConfidenceBadge value={e.confidence} /></div>))}</div>
              )}
              {current === "warnings" && (
                <div className="space-y-2">
                  {warnings.length + errors.length === 0 && <EmptyState title="No warnings or errors were returned" />}
                  {errors.map((e, i) => <div key={`e${i}`} className="card card-pad text-sm text-danger"><span className="chip mr-1">{e.code}</span>{e.message}</div>)}
                  {warnings.map((w, i) => <div key={`w${i}`} className="card card-pad text-sm"><span className="chip mr-1">{w.code}</span>{w.message}</div>)}
                </div>
              )}
              {current === "map" && <DocumentMap pages={bundle.pages} />}
              {current === "virtual" && virtual.data && (
                <div className="space-y-2"><p className="text-sm text-muted">Virtual document {virtual.data.virtual_document_id}</p>
                  <ul className="space-y-1">{virtual.data.pages.map((p) => {
                    const name = b.sources.find((s) => s.source_id === p.source_id)?.filename ?? p.source_id;
                    return <li key={p.virtual_page_number}>
                      {p.boundary_start && <div className="mt-3 border-t-2 border-accent pt-1 text-xs font-semibold text-accent">Start of {name}</div>}
                      <Link className="card flex justify-between p-2 text-sm hover:bg-surface2" to={`/sources/${encodeURIComponent(p.source_id)}?page=${p.page_number}`}><span>Virtual page {p.virtual_page_number}</span><span className="text-muted">{name} · page {p.page_number}</span></Link></li>;
                  })}</ul></div>
              )}
              {current === "combined" && caseQ.data && (
                <div className="space-y-6"><FactsTable facts={caseQ.data.facts} /><ComparisonsList comparisons={caseQ.data.comparisons} notComparable={caseQ.data.not_comparable} facts={caseQ.data.facts} /></div>
              )}
              {current === "case" && caseQ.data && (
                <div className="space-y-3"><p className="text-sm">Case: <Link className="text-accent hover:underline" to={`/cases/${encodeURIComponent(caseQ.data.case_id)}`}>{caseQ.data.name}</Link></p>
                  {caseQ.data.findings.map((f) => <FindingCard key={f.finding_id} f={f} caseId={caseQ.data.case_id} />)}
                  {caseQ.data.findings.length === 0 && <EmptyState title="No findings were returned for this case" />}</div>
              )}
              {current === "actions" && caseQ.data && (
                caseQ.data.actions.length === 0 ? <EmptyState title="No action drafts yet" /> :
                  <ul className="space-y-2">{caseQ.data.actions.map((a) => <li key={a.action_id}><Link className="card flex justify-between p-3 text-sm hover:bg-surface2" to={`/cases/${encodeURIComponent(a.case_id)}/actions/${encodeURIComponent(a.action_id)}`}><span>{a.draft_payload.subject ?? a.action_type}</span><span className="chip">{a.status}</span></Link></li>)}</ul>
              )}
              {current === "exports" && <ExportPanel defaultScopeIds={activeId ? [activeId] : []} />}
              {current === "audit" && <AuditTable fixed={{ object_id: activeId }} />}
            </div>
          </>
        )}
      </QueryBoundary>
      {visualPreview && (
        <div className="fixed inset-0 z-[100] grid place-items-center bg-black/80 p-4" role="presentation" onMouseDown={() => setVisualPreview(null)}>
          <section className="card w-full max-w-6xl overflow-hidden shadow-2xl" role="dialog" aria-modal="true" aria-label={visualPreview.alt} onMouseDown={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between gap-3 border-b border-border p-3">
              <h2 className="truncate font-medium">{visualPreview.alt}</h2>
              <button type="button" className="btn btn-sm" onClick={() => setVisualPreview(null)}>Close</button>
            </div>
            <div className="grid min-h-40 place-items-center bg-surface2 p-3">
              <AuthedImage src={visualPreview.src} alt={visualPreview.alt} className="max-h-[82vh] w-full object-contain" />
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
