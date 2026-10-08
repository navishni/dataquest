import { useQuery } from "@tanstack/react-query";
import { getSourcePage } from "@/api/platform";
import { AuthedImage } from "@/components/common/AuthedImage";
import { ErrorState, Skeleton } from "@/components/common/StateViews";
import type { EvidenceReference } from "@/types/canonical";

/** Shows the part of the source page that an evidence reference points at (bbox from the backend, image from the backend). */
export function EvidenceCrop({ e }: { e: EvidenceReference }) {
  const q = useQuery({ queryKey: ["page", e.source_id, e.page_number], queryFn: ({ signal }) => getSourcePage(e.source_id, e.page_number, signal) });
  if (q.isPending) return <Skeleton lines={3} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const p = q.data;
  if (!e.bbox) return <p className="text-sm text-muted">No location was returned for this evidence.</p>;
  const [x1, y1, x2, y2] = e.bbox;
  const pad = 24;
  const cx = Math.max(0, x1 - pad), cy = Math.max(0, y1 - pad);
  const cw = Math.min(p.width, x2 + pad) - cx, ch = Math.min(p.height, y2 + pad) - cy;
  const scale = 100 / cw; // percent of container width per page px
  return (
    <div className="relative w-full overflow-hidden rounded border border-border" style={{ aspectRatio: `${cw} / ${ch}` }}>
      <div className="absolute" style={{ width: `${p.width * scale}%`, left: `${-cx * scale}%`, top: `${(-cy * scale * cw) / ch}%` }}>
        <div className="relative">
          <AuthedImage src={p.image_url} alt={`${e.filename} page ${e.page_number}`} className="block w-full" />
          <div className="absolute border-2 border-accent bg-accent/10" style={{
            left: `${(x1 / p.width) * 100}%`, top: `${(y1 / p.height) * 100}%`,
            width: `${((x2 - x1) / p.width) * 100}%`, height: `${((y2 - y1) / p.height) * 100}%`,
          }} />
        </div>
      </div>
    </div>
  );
}
