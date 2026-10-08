import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AGENT_ENDPOINTS } from "@/agents";
import { apiRequestRaw } from "@/api/client";
import { NotConnectedError } from "@/api/errors";
import { getHealth } from "@/api/platform";
import { PageHeader } from "@/components/common/Primitives";
import { QueryBoundary } from "@/components/common/StateViews";

type Result = "connected" | "not-connected";

export function AgentStatusPage() {
  const q = useQuery({ queryKey: ["health"], queryFn: ({ signal }) => getHealth(signal) });
  const [results, setResults] = useState<Record<string, Result>>({});
  const [running, setRunning] = useState(false);

  // Sends an empty request to each endpoint. Any envelope response (even a validation error) proves the route exists.
  const check = async () => {
    setRunning(true); setResults({});
    for (const ep of AGENT_ENDPOINTS) {
      let r: Result = "connected";
      try {
        await apiRequestRaw(ep.path, ep.method === "POST" ? { method: "POST", json: {} } : {});
      } catch (e) { if (e instanceof NotConnectedError) r = "not-connected"; }
      setResults((o) => ({ ...o, [`${ep.method} ${ep.path}`]: r }));
    }
    setRunning(false);
  };
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader title="Agent status" actions={<button className="btn btn-primary" disabled={running} onClick={() => void check()}>{running ? "Checking…" : "Run contract check"}</button>} />
      <section><h2 className="mb-2 font-semibold">Reported health</h2>
        <QueryBoundary query={q}>{(d) => (
          <ul className="grid gap-2 sm:grid-cols-2">{d.agents.map((a) => (
            <li key={a.id} className="card flex justify-between p-3 text-sm"><span>{a.name}</span><span><span className="chip">{a.status}</span>{a.latency_ms !== undefined && <span className="ml-2 text-xs text-muted">{a.latency_ms} ms</span>}</span></li>))}</ul>)}</QueryBoundary></section>
      <section><h2 className="mb-2 font-semibold">Contract check</h2>
        <p className="mb-2 text-sm text-muted">The check sends an empty request to each endpoint the app uses. An error reply still shows the route exists.</p>
        <table className="w-full text-sm"><thead><tr className="text-left text-muted"><th className="py-1">Agent</th><th>Endpoint</th><th>Result</th></tr></thead>
          <tbody>{AGENT_ENDPOINTS.map((ep) => { const r = results[`${ep.method} ${ep.path}`]; return (
            <tr key={ep.method + ep.path} className="border-t border-border"><td className="py-1">{ep.agent}</td><td><code>{ep.method} {ep.path}</code></td>
              <td>{r === "connected" ? "Connected" : r === "not-connected" ? `Backend not connected: ${ep.path}` : "Not checked"}</td></tr>); })}</tbody></table></section>
    </div>
  );
}
