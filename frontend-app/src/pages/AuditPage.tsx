import { PageHeader } from "@/components/common/Primitives";
import { AuditTable } from "@/components/common/AuditTable";

export function AuditPage() {
  return (<div><PageHeader title="Audit log" subtitle="Append-only, hash-chained history of activity." /><AuditTable showFilters /></div>);
}
