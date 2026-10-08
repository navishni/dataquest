import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Flame, Maximize2, ZoomIn, ZoomOut, ScanLine } from "lucide-react";
import { a21 } from "@/agents";
import { ConfidenceBadge } from "@/components/common/ConfidenceBadge";
import { PageHeader, Tabs } from "@/components/common/Primitives";
import { ErrorState, Skeleton } from "@/components/common/StateViews";
import { BlockList } from "@/components/viewer/BlockList";
import { BlockPanel } from "@/components/viewer/BlockPanel";
import { PageOverlay } from "@/components/viewer/PageOverlay";
import { TableGrid } from "@/components/tables/TableGrid";
import { getSource, getSourcePage } from "@/api/platform";
import { isTableBlock, type BoundingBox } from "@/types/canonical";

export function SourceViewerPage() {
  const { sourceId = "" } = useParams();
  const [sp, setSp] = useSearchParams();
  const pageNo = Math.max(1, Number(sp.get("page") ?? "1") || 1);
  const blockParam = sp.get("block");
  const cellParam = sp.get("cell");
  const [zoom, setZoom] = useState(1);
  const [heatmap, setHeatmap] = useState(false);
  const [uncovered, setUncovered] = useState(false);
  const [side, setSide] = useState<"details" | "blocks">("details");

  const doc = useQuery({ queryKey: ["source", sourceId], queryFn: ({ signal }) => getSource(sourceId, signal) });
  const page = useQuery({ queryKey: ["page", sourceId, pageNo], queryFn: ({ signal }) => getSourcePage(sourceId, pageNo, signal) });
  // Coverage (agent 21) is loaded only when the person asks to see uncovered regions.
  const coverage = useQuery({
    queryKey: ["coverage", sourceId, pageNo], enabled: uncovered && !page.data?.uncovered_regions,
    queryFn: () => a21.runConsensus({ source_id: sourceId, page_number: pageNo }),
  });

  const setParam = (k: string, v: string | null) => setSp((old) => { const n = new URLSearchParams(old); if (v === null) n.delete(k); else n.set(k, v); return n; }, { replace: true });
  const select = (id: string | null) => { setSp((old) => { const n = new URLSearchParams(old); if (id) n.set("block", id); else n.delete("block"); n.delete("cell"); return n; }, { replace: true }); if (id) setSide("details"); };
  const goPage = (n: number) => setSp((old) => { const m = new URLSearchParams(old); m.set("page", String(n)); m.delete("block"); m.delete("cell"); return m; });

  const total = doc.data?.page_count ?? 0;
  const selected = page.data?.blocks.find((b) => b.block_id === blockParam) ?? null;
  const pageForOverlay = useMemo(() => {
    if (!page.data) return undefined;
    const cov = coverage.data?.coverage.find((c) => c.page_number === pageNo);
    return page.data.uncovered_regions ? page.data : { ...page.data, uncovered_regions: cov?.uncovered_regions };
  }, [page.data, coverage.data, pageNo]);

  const cellBox: BoundingBox | null = useMemo(() => {
    if (!selected || !isTableBlock(selected) || !cellParam) return null;
    const [r, c] = cellParam.split("-").map(Number);
    return selected.cells.find((x) => x.row === r && x.col === c)?.location ?? null;
  }, [selected, cellParam]);

  useEffect(() => {
    if (blockParam) document.getElementById(`blk-${blockParam}`)?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }, [blockParam, page.data]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT") return;
      const blocks = [...(page.data?.blocks ?? [])].sort((a, b) => a.reading_order_index - b.reading_order_index);
      const idx = blocks.findIndex((b) => b.block_id === blockParam);
      if (e.key === "ArrowLeft" && pageNo > 1) goPage(pageNo - 1);
      else if (e.key === "ArrowRight" && pageNo < total) goPage(pageNo + 1);
      else if (e.key === "+" || e.key === "=") setZoom((z) => Math.min(4, z + 0.25));
      else if (e.key === "-") setZoom((z) => Math.max(0.25, z - 0.25));
      else if (e.key === "h") setHeatmap((v) => !v);
      else if (e.key === "u") setUncovered((v) => !v);
      else if (e.key === "j") select(blocks[Math.min(blocks.length - 1, idx + 1)]?.block_id ?? null);
      else if (e.key === "k") select(blocks[Math.max(0, idx - 1)]?.block_id ?? null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={doc.data?.filename ?? "Source viewer"}
        subtitle={doc.data ? `${doc.data.kind} · ${total} page${total === 1 ? "" : "s"}` : sourceId}
        actions={doc.data?.document_confidence !== undefined ? <ConfidenceBadge value={doc.data.document_confidence} /> : undefined}
      />
      <div className="mb-3 flex flex-wrap items-center gap-2" role="toolbar" aria-label="Viewer controls">
        <button className="btn btn-sm" aria-label="Previous page" disabled={pageNo <= 1} onClick={() => goPage(pageNo - 1)}><ChevronLeft size={14} /></button>
        <span className="text-sm" aria-live="polite">Page {pageNo}{total ? ` of ${total}` : ""}</span>
        <button className="btn btn-sm" aria-label="Next page" disabled={total > 0 && pageNo >= total} onClick={() => goPage(pageNo + 1)}><ChevronRight size={14} /></button>
        <span className="mx-2 h-5 w-px bg-border" />
        <button className="btn btn-sm" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}><ZoomOut size={14} /></button>
        <button className="btn btn-sm" aria-label="Fit to width" onClick={() => setZoom(1)}><Maximize2 size={14} /></button>
        <button className="btn btn-sm" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(4, z + 0.25))}><ZoomIn size={14} /></button>
        <span className="mx-2 h-5 w-px bg-border" />
        <button className={`btn btn-sm ${heatmap ? "btn-primary" : ""}`} aria-pressed={heatmap} onClick={() => setHeatmap(!heatmap)}><Flame size={14} /> Confidence heatmap</button>
        <button className={`btn btn-sm ${uncovered ? "btn-primary" : ""}`} aria-pressed={uncovered} onClick={() => setUncovered(!uncovered)}><ScanLine size={14} /> Uncovered regions</button>
        <span className="ml-auto hidden text-xs text-muted lg:inline">Shortcuts: ← → page · + − zoom · h heatmap · u uncovered · j k next/previous block</span>
      </div>
      {doc.isError && <ErrorState error={doc.error} onRetry={() => void doc.refetch()} />}
      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[1fr,380px]">
        <div className="min-h-[50vh] overflow-auto rounded-lg border border-border bg-surface2 p-3">
          {page.isPending && <Skeleton lines={8} />}
          {page.isError && <ErrorState error={page.error} onRetry={() => void page.refetch()} />}
          {pageForOverlay && <PageOverlay page={pageForOverlay} selectedBlockId={blockParam} onSelect={select} heatmap={heatmap} showUncovered={uncovered} zoom={zoom} highlightBox={cellBox} />}
          {uncovered && coverage.isError && <ErrorState error={coverage.error} />}
        </div>
        <aside className="card flex min-h-0 flex-col">
          <Tabs tabs={[{ id: "details", label: "Details" }, { id: "blocks", label: `Blocks${page.data ? ` (${page.data.blocks.length})` : ""}` }]} value={side} onChange={setSide} />
          <div className="min-h-0 flex-1 overflow-auto p-3">
            {side === "blocks" && page.data && <BlockList blocks={page.data.blocks} selectedId={blockParam} onSelect={select} />}
            {side === "details" && (selected ? (
              <>
                <BlockPanel block={selected} />
                {isTableBlock(selected) && (
                  <div className="mt-4"><h3 className="label">Table</h3>
                    <TableGrid table={selected} selected={cellParam ? (() => { const [r, c] = cellParam.split("-").map(Number); return { row: r ?? -1, col: c ?? -1 }; })() : null}
                      onSelectCell={(c) => c.source_page_number && c.source_page_number !== pageNo
                        ? goPage(c.source_page_number)
                        : setParam("cell", `${c.row}-${c.col}`)} /></div>
                )}
              </>
            ) : (
              <div className="space-y-2 text-sm text-muted">
                <p>Select a block on the page to see its details.</p>
                {page.data && <p>Layout: {page.data.layout_class}. Reading order confidence: <ConfidenceBadge value={page.data.reading_order_confidence} />
                  {page.data.coverage_score !== undefined && <> Coverage: <ConfidenceBadge value={page.data.coverage_score} showLabel={false} /></>}</p>}
                {doc.data?.warnings.map((w, i) => <p key={i} className="text-warn"><span className="chip mr-1">{w.code}</span>{w.message}</p>)}
              </div>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}
