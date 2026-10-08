import { useMemo } from "react";
import { AuthedImage } from "@/components/common/AuthedImage";
import { useConfig } from "@/hooks/useApp";
import { bandFor, tokenColor } from "@/lib/bands";
import type { Block, BoundingBox, PageUnit } from "@/types/canonical";

interface Props {
  page: PageUnit;
  selectedBlockId: string | null;
  onSelect: (id: string) => void;
  heatmap: boolean;
  showUncovered: boolean;
  zoom: number;
  highlightBox?: BoundingBox | null;
}

function toPageBox(loc: BoundingBox, page: PageUnit): [number, number, number, number] | null {
  if (!loc.bbox) return null;
  const sx = loc.page_width ? page.width / loc.page_width : 1;
  const sy = loc.page_height ? page.height / loc.page_height : 1;
  const [x1, y1, x2, y2] = loc.bbox;
  return [x1 * sx, y1 * sy, x2 * sx, y2 * sy];
}

/** Page image with an SVG overlay in page pixel space; it scales with the rendered size via viewBox. */
export function PageOverlay({ page, selectedBlockId, onSelect, heatmap, showUncovered, zoom, highlightBox }: Props) {
  const { confidence_bands } = useConfig();
  const boxes = useMemo(
    () => page.blocks.map((b: Block) => ({ b, box: toPageBox(b.location, page) })),
    [page],
  );
  return (
    <div className="relative" style={{ width: `${zoom * 100}%`, minWidth: 200 }}>
      <AuthedImage src={page.image_url} alt={`Page ${page.page_number}`} className="block w-full select-none" />
      <svg viewBox={`0 0 ${page.width} ${page.height}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full" role="group" aria-label="Detected blocks">
        <defs>
          <pattern id="locked-pattern" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="12" height="12" fill="rgb(var(--surface2))" /><rect width="6" height="12" fill="rgb(var(--border))" />
          </pattern>
        </defs>
        {showUncovered && page.uncovered_regions?.map((r, i) => {
          const bb = toPageBox(r, page);
          return bb ? <rect key={`u${i}`} x={bb[0]} y={bb[1]} width={bb[2] - bb[0]} height={bb[3] - bb[1]} fill="none" stroke="rgb(var(--warn))" strokeWidth={3} strokeDasharray="10 6" vectorEffect="non-scaling-stroke"><title>Region not covered by any block</title></rect> : null;
        })}
        {boxes.map(({ b, box }) => {
          if (!box) return null;
          const s = bandFor(b.confidence, confidence_bands);
          const selected = b.block_id === selectedBlockId;
          const fill = b.locked ? "url(#locked-pattern)" : heatmap ? tokenColor(s.token, 0.38) : "transparent";
          return (
            <rect
              key={b.block_id} id={`blk-${b.block_id}`} x={box[0]} y={box[1]} width={box[2] - box[0]} height={box[3] - box[1]}
              fill={fill} stroke={selected ? "rgb(var(--accent))" : tokenColor(heatmap ? s.token : "info", 0.9)}
              strokeWidth={selected ? 3 : 1.5} vectorEffect="non-scaling-stroke" className="cursor-pointer"
              tabIndex={0} role="button"
              aria-label={`${b.type} block${b.locked ? ", locked" : ""}${s.band ? `, confidence ${s.band.label}` : ""}${selected ? ", selected" : ""}`}
              onClick={() => onSelect(b.block_id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(b.block_id); } }}
            />
          );
        })}
        {highlightBox && toPageBox(highlightBox, page) && (() => {
          const bb = toPageBox(highlightBox, page)!;
          return <rect x={bb[0]} y={bb[1]} width={bb[2] - bb[0]} height={bb[3] - bb[1]} fill="rgb(var(--accent) / 0.2)" stroke="rgb(var(--accent))" strokeWidth={3} vectorEffect="non-scaling-stroke" pointerEvents="none" />;
        })()}
      </svg>
    </div>
  );
}
