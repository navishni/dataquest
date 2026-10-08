import { Link } from "react-router-dom";
import { useConfig } from "@/hooks/useApp";
import { bandFor, tokenColor } from "@/lib/bands";
import type { PageUnit } from "@/types/canonical";

/** Page-by-page strip built only from block data: one cell per block, colour = confidence band, text = block type. */
export function DocumentMap({ pages }: { pages: PageUnit[] }) {
  const { confidence_bands } = useConfig();
  return (
    <div className="space-y-2">
      {pages.map((p) => (
        <div key={p.page_id} className="flex items-center gap-3">
          <Link to={`/sources/${encodeURIComponent(p.source_id)}?page=${p.page_number}`} className="w-16 shrink-0 text-xs text-accent hover:underline">Page {p.page_number}</Link>
          <div className="flex flex-1 flex-wrap gap-1">
            {[...p.blocks].sort((a, b) => a.reading_order_index - b.reading_order_index).map((b) => {
              const s = bandFor(b.confidence, confidence_bands);
              return (
                <Link
                  key={b.block_id} to={`/sources/${encodeURIComponent(p.source_id)}?page=${p.page_number}&block=${encodeURIComponent(b.block_id)}`}
                  className="rounded border px-1.5 py-0.5 text-[11px]" style={{ background: tokenColor(s.token, 0.25), borderColor: tokenColor(s.token, 0.7) }}
                  title={`${b.type}${s.band ? `, ${s.band.label}` : ""}`}
                >{b.type}{s.band ? ` · ${s.band.label}` : ""}</Link>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
