import { useState } from "react";
import { AdminQueue, PreviewTable, SchemaBrowser } from "@/components/access/AccessPanels";
import { PageHeader } from "@/components/common/Primitives";
import { CAP } from "@/config/capabilityKeys";
import { useCan } from "@/hooks/useApp";

export function AccessPage() {
  const can = useCan();
  const [sel, setSel] = useState<string | null>(null);
  const canManage = can(CAP.manageAccess);
  const canRequest = can(CAP.requestAccess);
  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <PageHeader
        title="Access and data visibility"
        subtitle={canManage
          ? "Choose a data table, control what viewers can see, and preview its available data."
          : "Browse your available data or request temporary access to hidden columns. An admin reviews each request."}
      />

      <section className="card card-pad space-y-5">
        <div className="flex items-start gap-3">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/10 text-xs font-semibold text-accent">1</span>
          <div><h2 className="font-semibold">Choose a data table</h2><p className="mt-1 text-sm text-muted">Select a table to review its columns and viewer visibility.</p></div>
        </div>
        <SchemaBrowser selected={sel} onSelect={setSel} />
      </section>

      <section className="card card-pad space-y-4">
        <div className="flex items-start gap-3">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/10 text-xs font-semibold text-accent">2</span>
          <div><h2 className="font-semibold">Data preview</h2><p className="mt-1 text-sm text-muted">Restricted values remain hidden until access is approved.</p></div>
        </div>
        {sel ? <PreviewTable resourceId={sel} /> : <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted">Choose a data table above to see your preview.</p>}
      </section>

      {(can(CAP.admin) || canRequest) && (
        <section className="card card-pad space-y-4">
          <div className="flex items-start gap-3">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/10 text-xs font-semibold text-accent">3</span>
            <div><h2 className="font-semibold">Access requests</h2><p className="mt-1 text-sm text-muted">{can(CAP.admin) ? "Review requests and approve access for a limited time." : "Track your requests and their approval status."}</p></div>
          </div>
          <AdminQueue />
        </section>
      )}
    </div>
  );
}
