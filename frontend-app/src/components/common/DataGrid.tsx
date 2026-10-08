import { useRef, type ReactNode } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Lock } from "lucide-react";

export interface GridCol<T> { id: string; header: string; locked?: boolean; width?: string; cell: (row: T) => ReactNode }

/** Virtualized table. Locked columns render a striped placeholder and never call `cell`, so hidden values are never touched. */
export function DataGrid<T>({ cols, rows, rowHeight = 36, maxHeight = 480, empty }: {
  cols: GridCol<T>[]; rows: T[]; rowHeight?: number; maxHeight?: number; empty?: ReactNode;
}) {
  const parent = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({ count: rows.length, getScrollElement: () => parent.current, estimateSize: () => rowHeight, overscan: 10 });
  const template = cols.map((c) => c.width ?? "minmax(120px,1fr)").join(" ");
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div ref={parent} className="overflow-auto" style={{ maxHeight }} role="table" aria-rowcount={rows.length}>
        <div className="sticky top-0 z-10 grid border-b border-border bg-surface2" style={{ gridTemplateColumns: template, minWidth: "max-content" }} role="row">
          {cols.map((c) => (
            <div key={c.id} role="columnheader" className="th flex items-center gap-1">{c.locked && <Lock size={12} aria-label="Locked column" />}{c.header}</div>
          ))}
        </div>
        {rows.length === 0 ? (empty ?? <p className="p-4 text-sm text-muted">No rows.</p>) : (
          <div style={{ height: v.getTotalSize(), position: "relative", minWidth: "max-content" }}>
            {v.getVirtualItems().map((vi) => {
              const row = rows[vi.index] as T;
              return (
                <div key={vi.index} role="row" className="absolute left-0 grid w-full border-b border-border text-sm hover:bg-surface2"
                  style={{ gridTemplateColumns: template, height: vi.size, transform: `translateY(${vi.start}px)` }}>
                  {cols.map((c) => (
                    <div key={c.id} role="cell" className="flex items-center overflow-hidden px-3">
                      {c.locked ? <span className="locked-stripes h-4 w-20 rounded" aria-label="Locked value" /> : <span className="truncate">{c.cell(row)}</span>}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
