import { Link } from "react-router-dom";
import { FileText } from "lucide-react";
import type { EvidenceReference } from "@/types/canonical";
import { ConfidenceBadge } from "@/components/common/ConfidenceBadge";

/** Click-to-source: any value that carries an EvidenceReference opens the viewer on that page with the box highlighted. */
export function evidencePath(e: Pick<EvidenceReference, "source_id" | "page_number" | "block_id">, cell?: string): string {
  const q = new URLSearchParams({ page: String(e.page_number), block: e.block_id });
  if (cell) q.set("cell", cell);
  return `/sources/${encodeURIComponent(e.source_id)}?${q.toString()}`;
}

export function EvidenceChip({ e }: { e: EvidenceReference }) {
  return (
    <Link to={evidencePath(e)} className="card block p-2 text-sm hover:bg-surface2" aria-label={`Open source ${e.filename} page ${e.page_number}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex min-w-0 items-center gap-1 font-medium"><FileText size={14} aria-hidden /><span className="truncate">{e.filename}</span>
          <span className="chip">p. {e.page_number}</span></span>
        <ConfidenceBadge value={e.confidence} showLabel={false} />
      </div>
      <p className="mt-1 line-clamp-3 break-words text-xs text-muted">{e.text_excerpt}</p>
    </Link>
  );
}

export function EvidenceList({ items }: { items: EvidenceReference[] }) {
  if (items.length === 0) return <p className="text-sm text-muted">No evidence references were returned.</p>;
  return <ul className="space-y-2">{items.map((e, i) => <li key={`${e.block_id}-${i}`}><EvidenceChip e={e} /></li>)}</ul>;
}
