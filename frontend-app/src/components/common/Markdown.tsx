import type { ReactNode } from "react";

// Minimal, safe Markdown renderer: everything becomes React text nodes (no HTML injection).
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(<strong key={i++}>{t.slice(2, -2)}</strong>);
    else if (t.startsWith("`")) out.push(<code key={i++} className="rounded bg-surface2 px-1">{t.slice(1, -1)}</code>);
    else out.push(<em key={i++}>{t.slice(1, -1)}</em>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const splitRow = (l: string) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export function Markdown({ source }: { source: string }) {
  const lines = source.split("\n");
  const nodes: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = (h[1] ?? "#").length;
      nodes.push(<p key={i} className={`mt-4 font-semibold ${level <= 2 ? "text-lg" : "text-base"}`} role="heading" aria-level={level}>{inline(h[2] ?? "")}</p>);
      i++;
    } else if (line.trim().startsWith("|") && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1] ?? "")) {
      const head = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && (lines[i] ?? "").trim().startsWith("|")) { rows.push(splitRow(lines[i] ?? "")); i++; }
      nodes.push(
        <div key={`t${i}`} className="my-3 overflow-auto"><table className="min-w-full border-collapse text-sm">
          <thead><tr>{head.map((c, j) => <th key={j} className="border border-border bg-surface2 px-2 py-1 text-left">{inline(c)}</th>)}</tr></thead>
          <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j} className="border border-border px-2 py-1">{inline(c)}</td>)}</tr>)}</tbody>
        </table></div>,
      );
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i] ?? "")) { items.push((lines[i] ?? "").replace(/^\s*[-*]\s+/, "")); i++; }
      nodes.push(<ul key={`u${i}`} className="my-2 list-disc pl-6 text-sm">{items.map((t, j) => <li key={j}>{inline(t)}</li>)}</ul>);
    } else if (line.trim() === "") {
      i++;
    } else {
      const para: string[] = [];
      while (i < lines.length && (lines[i] ?? "").trim() !== "" && !/^(#{1,6}\s|\s*[-*]\s|\s*\|)/.test(lines[i] ?? "")) { para.push(lines[i] ?? ""); i++; }
      nodes.push(<p key={`p${i}`} className="my-2 text-sm leading-relaxed">{inline(para.join(" "))}</p>);
    }
  }
  return <div>{nodes}</div>;
}
