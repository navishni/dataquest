import { useState } from "react";
import { ChevronDown, ChevronRight, Copy } from "lucide-react";
import { useToast } from "./Toast";

function Node({ k, v, depth }: { k: string | null; v: unknown; depth: number }) {
  const [open, setOpen] = useState(depth < 1);
  const isObj = typeof v === "object" && v !== null;
  const label = k !== null ? <span className="text-info">{k}: </span> : null;
  if (!isObj) {
    return <div className="pl-4 font-mono text-xs">{label}<span className={typeof v === "string" ? "text-ok" : ""}>{typeof v === "string" ? JSON.stringify(v) : String(v)}</span></div>;
  }
  const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v);
  return (
    <div className="pl-4 font-mono text-xs">
      <button className="inline-flex items-center gap-1 hover:underline" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}{label}
        <span className="text-muted">{Array.isArray(v) ? `[${entries.length}]` : `{${entries.length}}`}</span>
      </button>
      {open && entries.map(([ck, cv]) => <Node key={ck} k={ck} v={cv} depth={depth + 1} />)}
    </div>
  );
}

export function JsonTree({ value }: { value: unknown }) {
  const toast = useToast();
  return (
    <div>
      <div className="mb-2 flex justify-end">
        <button className="btn btn-sm" onClick={() => { void navigator.clipboard.writeText(JSON.stringify(value, null, 2)).then(() => toast.push("info", "JSON copied")); }}>
          <Copy size={12} /> Copy JSON
        </button>
      </div>
      <div className="max-h-[70vh] overflow-auto rounded-md border border-border bg-surface2 py-2"><Node k={null} v={value} depth={0} /></div>
    </div>
  );
}
