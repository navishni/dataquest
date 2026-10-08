// Display formatting only. No business results are computed here.
export function formatBytes(n: number): string {
  if (!Number.isFinite(n)) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`;
}

export function formatDateTime(iso: string | undefined | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(d);
}

/** A confidence value (0..1 as returned by the backend) shown as a percentage. */
export function formatConfidence(c: number | undefined | null): string {
  if (c === undefined || c === null || !Number.isFinite(c)) return "";
  return new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 }).format(c);
}

export function formatNumber(v: number, opts?: { currency?: string; unit?: string; locale?: string }): string {
  if (!Number.isFinite(v)) return String(v);
  try {
    if (opts?.currency) return new Intl.NumberFormat(opts.locale, { style: "currency", currency: opts.currency }).format(v);
  } catch {
    // unknown currency code: fall through to plain number
  }
  const base = new Intl.NumberFormat(opts?.locale, { maximumFractionDigits: 6 }).format(v);
  return opts?.unit ? `${base} ${opts.unit}` : base;
}

export function humanize(key: string): string {
  const s = key.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function displayValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}
