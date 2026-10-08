import { useQuery } from "@tanstack/react-query";
import { getMetrics } from "@/api/platform";
import { LineChart } from "@/components/common/LineChart";
import { PageHeader } from "@/components/common/Primitives";
import { EmptyState, QueryBoundary } from "@/components/common/StateViews";
import { formatNumber } from "@/lib/format";

export function MetricsPage() {
  const q = useQuery({ queryKey: ["metrics"], queryFn: ({ signal }) => getMetrics(signal) });
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Quality and metrics" />
      <QueryBoundary query={q} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="The backend returned no metrics" />}>
        {(d) => <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{d.items.map((m) => (
          <section key={m.id} className="card card-pad" aria-label={m.label}>
            <div className="text-sm text-muted">{m.label}</div>
            <div className="text-2xl font-semibold">{formatNumber(m.value, { unit: m.unit })}</div>
            {m.series && m.series.length > 1 && <LineChart label={m.label} points={m.series.map((p) => ({ x: p.t, y: p.v }))} />}
          </section>))}</div>}
      </QueryBoundary>
    </div>
  );
}
