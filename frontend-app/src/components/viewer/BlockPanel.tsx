import { useMutation } from "@tanstack/react-query";
import { Lock, EyeOff } from "lucide-react";
import { a21 } from "@/agents";
import { ConfidenceBadge } from "@/components/common/ConfidenceBadge";
import { AuthedImage } from "@/components/common/AuthedImage";
import { InlineError } from "@/components/common/StateViews";
import { KeyValue } from "@/components/common/Primitives";
import { LineChart } from "@/components/common/LineChart";
import { displayValue, formatNumber } from "@/lib/format";
import { isChartBlock, isEquationBlock, isFigureBlock, isTableBlock, type Block } from "@/types/canonical";

export function BlockPanel({ block }: { block: Block }) {
  const jury = useMutation({ mutationFn: () => a21.runConsensus({ source_id: block.source_id, block_id: block.block_id }) });
  const juryBlock = jury.data?.blocks.find((b) => b.block_id === block.block_id);
  const norm = block.normalized;
  return (
    <div className="space-y-4 text-sm" aria-label="Block details">
      <div className="flex flex-wrap items-center gap-2">
        <span className="chip font-semibold">{block.type}</span>
        <ConfidenceBadge value={block.confidence} />
        {block.needs_review && <span className="chip border-warn/50">Manual review recommended</span>}
        {block.locked && <span className="chip"><Lock size={12} aria-hidden /> Locked</span>}
        {block.masked && <span className="chip"><EyeOff size={12} aria-hidden /> Masked</span>}
      </div>

      {block.locked ? (
        <div className="locked-stripes flex h-20 items-center justify-center rounded text-muted" role="img" aria-label="Locked content"><Lock aria-hidden /></div>
      ) : (
        <>
          <section>
            <h3 className="label">Raw text</h3>
            <p className="whitespace-pre-wrap break-words rounded bg-surface2 p-2">{block.raw_text ?? block.raw_value ?? ""}</p>
          </section>
          {norm && (
            <section>
              <h3 className="label">Normalized value</h3>
              <KeyValue items={[
                { k: "Value", v: typeof norm.value === "number" ? formatNumber(norm.value, { currency: norm.currency, unit: norm.unit }) : displayValue(norm.value) },
                { k: "Rule", v: <code>{norm.rule}</code> },
              ]} />
            </section>
          )}
        </>
      )}

      <section>
        <h3 className="label">Extraction</h3>
        <KeyValue items={[
          { k: "Method", v: block.extraction_method },
          { k: "Block ID", v: <code className="text-xs">{block.block_id}</code> },
          { k: "Reading order", v: block.reading_order_index },
          ...(block.location.bbox_unavailable_reason ? [{ k: "Location", v: `Not available: ${block.location.bbox_unavailable_reason}` }] : []),
        ]} />
      </section>

      {block.confidence_breakdown && (
        <section>
          <h3 className="label">Confidence breakdown</h3>
          <ul className="space-y-1">
            {Object.entries(block.confidence_breakdown).map(([k, v]) => (
              <li key={k} className="flex items-center justify-between gap-2"><span className="text-muted">{k}</span><ConfidenceBadge value={v} showLabel={false} /></li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <div className="flex items-center justify-between">
          <h3 className="label">Parser jury</h3>
          <button className="btn btn-sm" disabled={jury.isPending} onClick={() => jury.mutate()}>Load consensus detail</button>
        </div>
        {block.alternatives && block.alternatives.length > 0 ? (
          <ul className="space-y-1">
            {block.alternatives.map((a, i) => (
              <li key={i} className="flex items-start justify-between gap-2 rounded border border-border p-2">
                <div className="min-w-0"><div className="text-xs font-semibold">{a.extractor}</div><div className="break-words">{a.value}</div></div>
                <ConfidenceBadge value={a.confidence} showLabel={false} />
              </li>
            ))}
          </ul>
        ) : <p className="text-muted">No alternative candidates were returned for this block.</p>}
        {jury.isError && <InlineError error={jury.error} />}
        {juryBlock && (
          <div className="mt-2 rounded border border-border p-2">
            <KeyValue items={[
              { k: "Winner", v: juryBlock.winner.value }, { k: "Winner extractor", v: juryBlock.winner.extractor },
              { k: "Agreement", v: juryBlock.agreement },
              { k: "Escalated", v: juryBlock.escalated ? "Yes" : "No" }, { k: "Needs review", v: juryBlock.needs_review ? "Yes" : "No" },
            ]} />
            <ul className="mt-2 space-y-1">{juryBlock.candidates.map((c, i) => <li key={i} className="flex justify-between gap-2"><span>{c.extractor}: {c.value}</span><ConfidenceBadge value={c.confidence} showLabel={false} /></li>)}</ul>
          </div>
        )}
      </section>

      {block.validation && block.validation.length > 0 && (
        <section>
          <h3 className="label">Validation checks</h3>
          <ul className="space-y-1">{block.validation.map((v, i) => (
            <li key={i} className="rounded border border-border p-2"><span className="font-medium">{v.name}</span> <span className="chip">{v.status}</span>{v.detail && <p className="mt-1 text-xs text-muted">{v.detail}</p>}</li>
          ))}</ul>
        </section>
      )}

      {block.warnings.length > 0 && (
        <section>
          <h3 className="label">Warnings</h3>
          <ul className="space-y-1">{block.warnings.map((w, i) => <li key={i} className="rounded border border-warn/40 p-2"><span className="chip mr-1">{w.code}</span>{w.message}</li>)}</ul>
        </section>
      )}

      {isTableBlock(block) && block.badges && block.badges.length > 0 && (
        <section><h3 className="label">Table badges</h3>
          <ul className="space-y-1">{block.badges.map((b, i) => <li key={i}><span className="chip mr-1">{b.status}</span>{b.label}{b.detail && <span className="text-muted"> - {b.detail}</span>}</li>)}</ul>
        </section>
      )}
      {(isChartBlock(block) || isFigureBlock(block) || isEquationBlock(block)) && !block.locked && (
        <section>
          <h3 className="label">Crop</h3>
          <AuthedImage src={block.crop_url} alt={`${block.type} crop`} className="max-h-60 rounded border border-border" />
          {isEquationBlock(block) && (
            <p className="mt-2 break-words font-mono text-xs">{block.latex ?? block.plain_text ?? ""} <span className="chip ml-1">{block.verified ? "Verified" : "Not verified"}</span></p>
          )}
          {isChartBlock(block) && (block.series?.length
            ? block.series.map((s) => <div key={s.name} className="mt-2 rounded border border-border p-2"><div className="mb-1 text-xs font-medium">{s.name}</div><LineChart points={s.points} label={`${s.name} series`} kind={/bar|column|histogram/.test((block.chart_type ?? "").toLowerCase()) ? "bar" : "line"} /></div>)
            : <p className="mt-2 text-xs text-muted">Chart values were not extracted; the source image is shown above.</p>)}
          {isChartBlock(block) && block.insight_text && <p className="mt-2">{block.insight_text}</p>}
        </section>
      )}
    </div>
  );
}
