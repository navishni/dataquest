import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { getBatch, listBatches, retryBatchSource } from "@/api/platform";
import { getJob, jobFinished } from "@/api/jobs";
import { PageHeader } from "@/components/common/Primitives";
import { EmptyState, InlineError, QueryBoundary } from "@/components/common/StateViews";
import { useConfig } from "@/hooks/useApp";
import { formatDateTime } from "@/lib/format";

export function BatchesPage() {
  const q = useQuery({ queryKey: ["batches"], queryFn: ({ signal }) => listBatches(signal) });
  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Batches" />
      <QueryBoundary query={q} isEmpty={(d) => d.length === 0} empty={<EmptyState title="No batches yet" hint="Start one from the Upload page." />}>
        {(d) => (
          <ul className="space-y-2">{d.map((b) => (
            <li key={b.batch_id} className="card flex items-center justify-between gap-3 p-3 text-sm">
              <div><Link className="font-medium text-accent hover:underline" to={`/batches/${encodeURIComponent(b.batch_id)}`}>{b.name ?? b.batch_id}</Link>
                <div className="text-xs text-muted">{formatDateTime(b.created_at)} · {b.sources.length} source{b.sources.length === 1 ? "" : "s"}</div></div>
              <Link className="btn btn-sm" to={`/batches/${encodeURIComponent(b.batch_id)}/results`}>Results</Link>
            </li>))}</ul>
        )}
      </QueryBoundary>
    </div>
  );
}

export function BatchProgressPage() {
  const { batchId = "" } = useParams();
  const { poll_interval_ms, pipeline_stages } = useConfig();
  const nav = useNavigate();
  const qc = useQueryClient();

  // The batch carries its job id; polling stops once the job reports a result or error (the UI does not interpret status words).
  const batch = useQuery({
    queryKey: ["batch", batchId], queryFn: ({ signal }) => getBatch(batchId, signal),
    refetchInterval: (q) => {
      const d = q.state.data;
      if (!d) return false;
      const allDone = d.sources.length > 0 && d.sources.every((s) => (s.progress_percent ?? 0) >= 100 || s.errors.length > 0);
      return allDone ? false : poll_interval_ms;
    },
  });
  const jobId = batch.data?.job_id;
  const job = useQuery({
    queryKey: ["job", jobId], queryFn: ({ signal }) => getJob(jobId!, signal), enabled: !!jobId,
    refetchInterval: (q) => (jobFinished(q.state.data) ? false : poll_interval_ms),
  });
  const retry = useMutation({
    mutationFn: (sid: string) => retryBatchSource(batchId, sid),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["batch", batchId] }),
  });
  const stageLabel = (id?: string) => (id ? (pipeline_stages.find((s) => s.id === id)?.label ?? id) : "");

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Batch progress" subtitle={batchId} actions={<button className="btn" onClick={() => nav(`/batches/${encodeURIComponent(batchId)}/results`)}>Open results</button>} />
      {job.data && (
        <div className="card card-pad mb-4 text-sm">
          <div className="flex justify-between"><span>{stageLabel(job.data.stage)}</span><span>{job.data.progress_percent !== undefined ? `${job.data.progress_percent}%` : ""}</span></div>
          {job.data.progress_percent !== undefined && <div className="mt-2 h-2 overflow-hidden rounded bg-surface2" role="progressbar" aria-valuenow={job.data.progress_percent} aria-valuemin={0} aria-valuemax={100}><div className="h-full bg-accent" style={{ width: `${job.data.progress_percent}%` }} /></div>}
          {job.data.error && <p className="mt-2 text-danger"><span className="chip mr-1">{job.data.error.code}</span>{job.data.error.message}</p>}
        </div>
      )}
      <QueryBoundary query={batch} isEmpty={(d) => d.sources.length === 0} empty={<EmptyState title="This batch has no sources" />}>
        {(d) => (
          <ul className="space-y-2">
            {d.sources.map((s) => (
              <li key={s.source_id} className="card card-pad text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0"><Link className="font-medium text-accent hover:underline" to={`/sources/${encodeURIComponent(s.source_id)}`}>{s.filename}</Link>
                    <span className="chip ml-2">{s.status}</span>{s.stage && <span className="chip ml-1">{stageLabel(s.stage)}</span>}</div>
                  {s.retryable && <button className="btn btn-sm" disabled={retry.isPending} onClick={() => retry.mutate(s.source_id)}><RotateCw size={12} /> Retry</button>}
                </div>
                {s.progress_percent !== undefined && <div className="mt-2 h-1.5 overflow-hidden rounded bg-surface2" role="progressbar" aria-valuenow={s.progress_percent} aria-valuemin={0} aria-valuemax={100} aria-label={`${s.filename} progress`}><div className="h-full bg-accent" style={{ width: `${s.progress_percent}%` }} /></div>}
                {s.warnings.map((w, i) => <p key={i} className="mt-1 text-warn"><span className="chip mr-1">{w.code}</span>{w.message}</p>)}
                {s.errors.map((e, i) => <p key={i} className="mt-1 text-danger"><span className="chip mr-1">{e.code}</span>{e.message}</p>)}
              </li>
            ))}
          </ul>
        )}
      </QueryBoundary>
      {retry.isError && <InlineError error={retry.error} />}
    </div>
  );
}
