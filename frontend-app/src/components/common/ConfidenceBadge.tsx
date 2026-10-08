import { AlertTriangle, CheckCircle2, CircleDot, HelpCircle } from "lucide-react";
import { useConfig } from "@/hooks/useApp";
import { bandFor, tokenColor } from "@/lib/bands";
import { formatConfidence } from "@/lib/format";

/** Confidence shown as value + band label + icon, so colour is never the only signal. */
export function ConfidenceBadge({ value, showLabel = true }: { value: number; showLabel?: boolean }) {
  const { confidence_bands } = useConfig();
  const s = bandFor(value, confidence_bands);
  const Icon = s.icon === "low" ? AlertTriangle : s.icon === "mid" ? CircleDot : s.icon === "high" ? CheckCircle2 : HelpCircle;
  return (
    <span className="chip" style={{ borderColor: tokenColor(s.token, 0.6), color: "rgb(var(--fg))" }} title={s.band?.label}>
      <Icon size={12} style={{ color: tokenColor(s.token) }} aria-hidden />
      {formatConfidence(value)}
      {showLabel && s.band && <span className="text-muted">{s.band.label}</span>}
    </span>
  );
}
