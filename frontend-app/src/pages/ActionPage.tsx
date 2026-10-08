import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getCase } from "@/api/cases";
import { ActionReview } from "@/components/approvals/ActionReview";
import { PageHeader } from "@/components/common/Primitives";
import { EmptyState, QueryBoundary } from "@/components/common/StateViews";

export function ActionPage() {
  const { caseId = "", actionId = "" } = useParams();
  const q = useQuery({ queryKey: ["case", caseId], queryFn: ({ signal }) => getCase(caseId, signal) });
  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Action review" subtitle="Drafts are proposals only. A person must review and approve every step."
        actions={<Link className="btn" to={`/cases/${encodeURIComponent(caseId)}`}>Back to case</Link>} />
      <QueryBoundary query={q}>
        {(c) => {
          const a = c.actions.find((x) => x.action_id === actionId);
          return a ? <ActionReview action={a} caseId={caseId} /> : <EmptyState title="This action draft was not found in the case" />;
        }}
      </QueryBoundary>
    </div>
  );
}
