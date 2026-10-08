import { formatNumber } from "@/lib/format";

export interface Pt { x: string | number; y: number }

/** Compact SVG chart preview with readable axes, labels, and hover values. */
export function LineChart({ points, label, kind = "line", height = 180 }: { points: Pt[]; label: string; kind?: "line" | "bar"; height?: number }) {
  const valid = points.filter((p) => Number.isFinite(p.y));
  if (valid.length === 0) return null;
  const w = 420;
  const left = 42;
  const right = 12;
  const top = 14;
  const bottom = valid.length <= 8 ? 34 : 14;
  const plotBottom = height - bottom;
  const ys = valid.map((p) => p.y);
  const min = Math.min(...ys, 0);
  const max = Math.max(...ys, 0);
  const span = max - min || 1;
  const sx = (i: number) => left + (valid.length === 1 ? (w - left - right) / 2 : (i * (w - left - right)) / (valid.length - 1));
  const sy = (v: number) => plotBottom - ((v - min) / span) * (plotBottom - top);
  const bw = Math.max(2, (w - left - right) / valid.length - 8);
  const ticks = [max, (max + min) / 2, min];
  return (
    <svg viewBox={`0 0 ${w} ${height}`} role="img" aria-label={label} className="w-full overflow-visible text-accent">
      <title>{label}</title>
      {ticks.map((value, i) => {
        const y = sy(value);
        return <g key={i} className="text-muted">
          <line x1={left} x2={w - right} y1={y} y2={y} stroke="currentColor" strokeOpacity={0.22} strokeDasharray={i === 2 ? undefined : "3 4"} />
          <text x={left - 6} y={y + 3} textAnchor="end" fontSize="9" fill="currentColor">{formatNumber(value)}</text>
        </g>;
      })}
      {kind === "bar"
        ? valid.map((p, i) => <rect key={i} x={sx(i) - bw / 2} y={Math.min(sy(p.y), sy(0))} width={bw} height={Math.max(1, Math.abs(sy(p.y) - sy(0)))} fill="currentColor" opacity={0.8}><title>{`${p.x}: ${formatNumber(p.y)}`}</title></rect>)
        : <>
            <polyline fill="none" stroke="currentColor" strokeWidth={2} points={valid.map((p, i) => `${sx(i)},${sy(p.y)}`).join(" ")} />
            {valid.map((p, i) => <circle key={i} cx={sx(i)} cy={sy(p.y)} r={3.5} fill="currentColor"><title>{`${p.x}: ${formatNumber(p.y)}`}</title></circle>)}
          </>}
      {valid.length <= 8 && valid.map((p, i) => <g key={`label-${i}`}>
        <text x={sx(i)} y={Math.max(top + 9, sy(p.y) - 7)} textAnchor="middle" fontSize="9" fill="currentColor">{formatNumber(p.y)}</text>
        <text x={sx(i)} y={height - 8} textAnchor="middle" fontSize="9" fill="currentColor">{String(p.x).slice(0, 12)}</text>
      </g>)}
    </svg>
  );
}
