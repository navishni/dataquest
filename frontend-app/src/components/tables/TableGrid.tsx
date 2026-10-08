import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Lock } from "lucide-react";
import { useConfig } from "@/hooks/useApp";
import { bandFor, tokenColor } from "@/lib/bands";
import { formatConfidence } from "@/lib/format";
import type { TableBlock, TableCell } from "@/types/canonical";

const ROWS_PER_PAGE = 100; // rendering window only; the total comes from the table itself

export function TableGrid({
  table, selected, onSelectCell, onOpenLinked,
}: {
  table: TableBlock;
  selected?: { row: number; col: number } | null;
  onSelectCell?: (c: TableCell) => void;
  onOpenLinked?: (blockId: string) => void;
}) {
  const { confidence_bands } = useConfig();
  const [start, setStart] = useState(0);
  const byRow = useMemo(() => {
    const m = new Map<number, TableCell[]>();
    for (const c of table.cells) { const arr = m.get(c.row) ?? []; arr.push(c); m.set(c.row, arr); }
    for (const arr of m.values()) arr.sort((a, b) => a.col - b.col);
    return m;
  }, [table.cells]);
  const end = Math.min(table.n_rows, start + ROWS_PER_PAGE);
  const rows = Array.from({ length: Math.max(0, end - start) }, (_, i) => start + i);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {table.caption && <span className="font-medium">{table.caption}</span>}
        {table.badges?.map((b, i) => <span key={i} className="chip" title={b.detail}>{b.label}: {b.status}</span>)}
        {table.continues_from && <button className="chip hover:bg-surface" onClick={() => onOpenLinked?.(table.continues_from!)}>Continues from previous table</button>}
        {table.continues_to && <button className="chip hover:bg-surface" onClick={() => onOpenLinked?.(table.continues_to!)}>Continues on next table</button>}
      </div>
      <div className="overflow-auto rounded border border-border">
        <table className="min-w-full border-collapse text-sm">
          <tbody>
            {rows.map((r) => (
              <tr key={r}>
                {(byRow.get(r) ?? []).map((c) => {
                  const s = bandFor(c.confidence, confidence_bands);
                  const sel = selected?.row === c.row && selected?.col === c.col;
                  const Tag = c.is_header ? "th" : "td";
                  return (
                    <Tag
                      key={`${c.row}-${c.col}`} rowSpan={c.row_span} colSpan={c.col_span} scope={c.is_header ? "col" : undefined}
                      className={`cursor-pointer border border-border px-2 py-1 text-left align-top ${c.is_header ? "bg-surface2 font-semibold" : ""} ${sel ? "outline outline-2 outline-accent" : ""}`}
                      style={c.is_header ? undefined : { background: tokenColor(s.token, 0.12) }}
                      title={`${s.band?.label ?? ""} ${formatConfidence(c.confidence)}${c.source_page_number ? ` · source page ${c.source_page_number}` : ""}`.trim()}
                      tabIndex={0} onClick={() => onSelectCell?.(c)}
                      onKeyDown={(e) => { if (e.key === "Enter") onSelectCell?.(c); }}
                    >
                      {c.locked ? <span className="locked-stripes inline-flex h-4 w-20 items-center justify-center rounded text-muted"><Lock size={11} aria-label="Locked cell" /></span> : c.raw_text}
                      {!c.locked && c.normalized != null && <div className="text-[11px] text-muted">{String(c.normalized.value)}</div>}
                    </Tag>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.n_rows > ROWS_PER_PAGE && (
        <div className="flex items-center justify-between text-xs text-muted">
          <span>Rows {start + 1}-{end} of {table.n_rows}</span>
          <span className="flex gap-1">
            <button className="btn btn-sm" aria-label="Previous rows" disabled={start === 0} onClick={() => setStart(Math.max(0, start - ROWS_PER_PAGE))}><ChevronLeft size={12} /></button>
            <button className="btn btn-sm" aria-label="Next rows" disabled={end >= table.n_rows} onClick={() => setStart(start + ROWS_PER_PAGE)}><ChevronRight size={12} /></button>
          </span>
        </div>
      )}
    </div>
  );
}
