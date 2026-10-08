import { useMemo } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { getSource, getSourcePage } from "@/api/platform";
import type { PageUnit, SourceDocument } from "@/types/canonical";

export interface SourceBundle {
  doc: SourceDocument | undefined;
  pages: PageUnit[];
  pending: boolean;
  error: unknown;
  refetch: () => void;
}

/** Loads a source and its pages (using pages embedded in the document when present, else fetching each page). */
export function useSourceBundle(sourceId: string | undefined): SourceBundle {
  const docQ = useQuery({ queryKey: ["source", sourceId], queryFn: ({ signal }) => getSource(sourceId!, signal), enabled: !!sourceId });
  const doc = docQ.data;
  const embedded = doc?.pages && doc.pages.length > 0 && doc.pages.every((p) => p.blocks.length > 0 || doc.pages!.length === doc.page_count);
  const pageNumbers = useMemo(
    () => (doc && !embedded ? Array.from({ length: doc.page_count }, (_, i) => i + 1) : []),
    [doc, embedded],
  );
  const pageQs = useQueries({
    queries: pageNumbers.map((n) => ({
      queryKey: ["page", sourceId, n], queryFn: ({ signal }: { signal: AbortSignal }) => getSourcePage(sourceId!, n, signal),
    })),
  });
  const pages = embedded ? (doc?.pages ?? []) : pageQs.flatMap((q) => (q.data ? [q.data] : []));
  const pending = docQ.isPending || (!embedded && pageQs.some((q) => q.isPending));
  const error = docQ.error ?? pageQs.find((q) => q.error)?.error ?? null;
  return { doc, pages, pending, error, refetch: () => { void docQ.refetch(); pageQs.forEach((q) => void q.refetch()); } };
}
