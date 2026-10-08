// Maps backend confidence bands to design-token colours by RANK (lowest band -> most cautionary), never by band id.
import type { ConfidenceBand } from "@/types/api";

export interface BandStyle { band: ConfidenceBand | null; rank: number; of: number; token: string; icon: "low" | "mid" | "high" | "none" }

const RAMP = ["danger", "warn", "info", "ok"] as const; // design tokens, low -> high

export function bandFor(confidence: number, bands: ConfidenceBand[]): BandStyle {
  const sorted = [...bands].sort((a, b) => a.min - b.min);
  const idx = sorted.findIndex((b) => confidence >= b.min && confidence <= b.max);
  const band = idx >= 0 ? (sorted[idx] ?? null) : null;
  if (!band) return { band: null, rank: -1, of: sorted.length, token: "muted", icon: "none" };
  const pos = sorted.length <= 1 ? 1 : idx / (sorted.length - 1);
  const token = RAMP[Math.min(RAMP.length - 1, Math.round(pos * (RAMP.length - 1)))] ?? "muted";
  const icon = pos < 0.34 ? "low" : pos < 0.84 ? "mid" : "high";
  return { band, rank: idx, of: sorted.length, token, icon };
}

export function tokenColor(token: string, alpha = 1): string {
  return `rgb(var(--${token}) / ${alpha})`;
}
