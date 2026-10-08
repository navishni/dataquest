export function SetupScreen() {
  return (
    <div className="mx-auto mt-16 max-w-xl card card-pad">
      <h1 className="text-xl font-semibold">Backend address not set</h1>
      <p className="mt-2 text-sm text-muted">
        ParseFusion needs the address of the backend. Create a file named <code>.env</code> next to <code>package.json</code>, set
        <code> VITE_API_BASE_URL</code> to the backend URL (for example the address where the API is served), then restart the dev server.
      </p>
      <pre className="mt-3 overflow-auto rounded bg-surface2 p-3 text-xs">VITE_API_BASE_URL=</pre>
      <p className="mt-3 text-sm text-muted">After the address is set, open the Agent status page to verify that every agent endpoint is reachable.</p>
    </div>
  );
}
