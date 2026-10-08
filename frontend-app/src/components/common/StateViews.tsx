import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { AlertTriangle, Inbox, PlugZap, RefreshCw } from "lucide-react";
import { ApiRequestError, ContractError, NotConnectedError } from "@/api/errors";

export function Skeleton({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={`animate-pulse space-y-2 ${className}`} role="status" aria-label="Loading">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="h-4 rounded bg-surface2" style={{ width: `${100 - ((i * 13) % 40)}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center text-muted">
      <Inbox size={28} aria-hidden />
      <p className="font-medium text-fg">{title}</p>
      {hint && <p className="max-w-md text-sm">{hint}</p>}
      {action}
    </div>
  );
}

export function NotConnectedState({ endpoint, reason, onRetry }: { endpoint: string; reason?: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 py-8 text-center" role="alert">
      <PlugZap size={28} className="text-warn" aria-hidden />
      <p className="font-medium">Backend not connected: <code className="rounded bg-surface2 px-1">{endpoint}</code></p>
      {reason && <p className="max-w-lg text-sm text-muted">{reason}</p>}
      {onRetry && <button className="btn btn-sm" onClick={onRetry}><RefreshCw size={12} /> Retry</button>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (error instanceof NotConnectedError) return <NotConnectedState endpoint={error.endpoint} reason={error.message} onRetry={onRetry} />;
  let code = "UNKNOWN";
  let message = error instanceof Error ? error.message : "Unexpected error";
  let requestId: string | undefined;
  let extra: string[] = [];
  if (error instanceof ApiRequestError) { code = error.code; message = error.message; requestId = error.requestId; }
  if (error instanceof ContractError) { code = "CONTRACT_MISMATCH"; extra = error.issues; }
  return (
    <div className="flex flex-col items-center gap-2 py-8 text-center" role="alert">
      <AlertTriangle size={28} className="text-danger" aria-hidden />
      <p className="font-medium"><span className="chip mr-2">{code}</span>{message}</p>
      {extra.length > 0 && <ul className="max-w-lg text-left text-xs text-muted">{extra.map((x) => <li key={x}>{x}</li>)}</ul>}
      {requestId && <p className="text-xs text-muted">Request ID: {requestId}</p>}
      {onRetry && <button className="btn btn-sm" onClick={onRetry}><RefreshCw size={12} /> Retry</button>}
    </div>
  );
}

/** Standard loading / error / not-connected / empty handling for a query. */
export function QueryBoundary<T>({
  query, children, isEmpty, empty, skeletonLines,
}: {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  skeletonLines?: number;
}) {
  if (query.isPending) return <Skeleton lines={skeletonLines} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  if (isEmpty?.(query.data)) return <>{empty ?? <EmptyState title="Nothing to show yet" />}</>;
  return <>{children(query.data)}</>;
}

export function InlineError({ error }: { error: unknown }) {
  if (error instanceof NotConnectedError) return <p role="alert" className="text-sm text-warn">Backend not connected: {error.endpoint}</p>;
  if (error instanceof ApiRequestError) {
    return <p role="alert" className="text-sm text-danger"><span className="chip mr-1">{error.code}</span>{error.message}{error.requestId ? ` (request ${error.requestId})` : ""}</p>;
  }
  return <p role="alert" className="text-sm text-danger">{error instanceof Error ? error.message : "Unexpected error"}</p>;
}
