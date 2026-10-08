import { useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Lock } from "lucide-react";
import { ConfidenceBadge } from "@/components/common/ConfidenceBadge";
import type { Block } from "@/types/canonical";

/** Virtualized block list, in reading order. */
export function BlockList({ blocks, selectedId, onSelect }: { blocks: Block[]; selectedId: string | null; onSelect: (id: string) => void }) {
  const parent = useRef<HTMLDivElement>(null);
  const sorted = [...blocks].sort((a, b) => a.reading_order_index - b.reading_order_index);
  const v = useVirtualizer({ count: sorted.length, getScrollElement: () => parent.current, estimateSize: () => 52, overscan: 8 });
  return (
    <div ref={parent} className="h-full overflow-auto" role="listbox" aria-label="Blocks in reading order">
      <div style={{ height: v.getTotalSize(), position: "relative" }}>
        {v.getVirtualItems().map((vi) => {
          const b = sorted[vi.index];
          if (!b) return null;
          return (
            <button
              key={b.block_id} role="option" aria-selected={b.block_id === selectedId} onClick={() => onSelect(b.block_id)}
              className={`absolute left-0 flex w-full items-center justify-between gap-2 border-b border-border px-3 text-left text-sm hover:bg-surface2 ${b.block_id === selectedId ? "bg-surface2" : ""}`}
              style={{ top: 0, transform: `translateY(${vi.start}px)`, height: vi.size }}
            >
              <span className="min-w-0">
                <span className="chip mr-2">{b.type}</span>
                <span className="truncate text-muted">{b.locked ? <Lock size={12} className="inline" aria-label="Locked" /> : (b.raw_text ?? b.raw_value ?? "").slice(0, 60)}</span>
              </span>
              <ConfidenceBadge value={b.confidence} showLabel={false} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
