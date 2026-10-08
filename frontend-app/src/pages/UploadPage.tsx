import { useCallback, useRef, useState, type DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, GripVertical, Link2, Trash2, UploadCloud, XCircle } from "lucide-react";
import { a01, a22 } from "@/agents";
import { createBatch, createCase, listCases } from "@/api/platform";
import { Field, PageHeader } from "@/components/common/Primitives";
import { InlineError } from "@/components/common/StateViews";
import { useToast } from "@/components/common/Toast";
import { useConfig } from "@/hooks/useApp";
import { formatBytes, humanize } from "@/lib/format";

interface Item {
  key: string; kind: "file" | "url"; name: string; size?: number; file?: File; url?: string;
  state: "checking" | "done" | "failed"; localProblem?: string; result?: a01.Output; urlResult?: a22.Output; error?: unknown; password?: string;
}

let seq = 0;
const nextKey = () => `i${++seq}`;

function typeAllowed(file: File, accepted: string[]): boolean {
  if (accepted.length === 0) return true;
  const ext = file.name.includes(".") ? file.name.slice(file.name.lastIndexOf(".")).toLowerCase() : "";
  return accepted.some((a) => {
    const t = a.toLowerCase();
    if (t.startsWith(".")) return t === ext;
    if (t.endsWith("/*")) return file.type.toLowerCase().startsWith(t.slice(0, -1));
    return t === file.type.toLowerCase();
  });
}

export function UploadPage() {
  const config = useConfig();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [items, setItems] = useState<Item[]>([]);
  const [drag, setDrag] = useState(false);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [mode, setMode] = useState(config.processing_modes[0]?.id ?? "");
  const [formats, setFormats] = useState<string[]>([]);
  const [caseId, setCaseId] = useState("");
  const [newCase, setNewCase] = useState("");
  const [instruction, setInstruction] = useState("");
  const [flags, setFlags] = useState<Record<string, boolean>>(config.feature_flags);
  const [urlText, setUrlText] = useState("");
  const [purpose, setPurpose] = useState("");
  const [basis, setBasis] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const cases = useQuery({ queryKey: ["cases"], queryFn: ({ signal }) => listCases(signal) });
  const casePlaceholder = cases.isError ? "Cases unavailable"
    : cases.isPending ? "Loading cases…"
      : cases.data?.length ? "No case selected" : "No existing cases yet";
  const patch = useCallback((key: string, p: Partial<Item>) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, ...p } : x))), []);

  const validate = useCallback(async (key: string, file: File, password?: string) => {
    patch(key, { state: "checking", error: undefined });
    try {
      const result = await a01.validateFile({ file, options: password ? { password } : undefined });
      patch(key, { state: "done", result });
    } catch (e) { patch(key, { state: "failed", error: e }); }
  }, [patch]);

  const addFiles = (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      const key = nextKey();
      const tooBig = file.size > config.limits.max_file_mb * 1024 * 1024;
      const badType = !typeAllowed(file, config.limits.accepted_types);
      const problem = tooBig ? `Larger than the ${config.limits.max_file_mb} MB limit` : badType ? "This file type is not accepted" : undefined;
      setItems((xs) => [...xs, { key, kind: "file", name: file.name, size: file.size, file, state: problem ? "failed" : "checking", localProblem: problem }]);
      if (!problem) void validate(key, file);
    }
  };

  const addUrls = useMutation({
    mutationFn: async () => {
      const urls = urlText.split(/\s+/).map((u) => u.trim()).filter(Boolean);
      for (const url of urls) {
        const key = nextKey();
        setItems((xs) => [...xs, { key, kind: "url", name: url, url, state: "checking" }]);
        try {
          const urlResult = await a22.ingestUrl({ url, purpose: purpose || undefined, authorization_basis: basis || undefined });
          patch(key, { state: "done", urlResult });
        } catch (e) { patch(key, { state: "failed", error: e }); }
      }
    },
    onSuccess: () => setUrlText(""),
  });

  const sourceIds = items.flatMap((i) => (i.kind === "file" && i.result?.status === "accepted" && i.result.source_id ? [i.result.source_id] : i.kind === "url" && i.urlResult?.status === "allowed" && i.urlResult.source_id ? [i.urlResult.source_id] : []));

  const start = useMutation({
    mutationFn: async () => {
      let cid = caseId || undefined;
      if (!cid && newCase.trim()) cid = (await createCase({ name: newCase.trim() })).case_id;
      return createBatch({ source_ids: sourceIds, mode, output_formats: formats, case_id: cid, instruction: instruction.trim() || undefined, options: flags });
    },
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["batches"] }); void qc.invalidateQueries({ queryKey: ["cases"] }); toast.push("info", "Batch started"); nav(`/batches/${encodeURIComponent(r.batch_id)}`); },
  });

  const onDropFiles = (e: DragEvent) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); };
  const reorder = (overKey: string) => {
    if (!dragKey || dragKey === overKey) return;
    setItems((xs) => {
      const from = xs.findIndex((x) => x.key === dragKey), to = xs.findIndex((x) => x.key === overKey);
      if (from < 0 || to < 0) return xs;
      const copy = [...xs]; const [moved] = copy.splice(from, 1); copy.splice(to, 0, moved as Item); return copy;
    });
  };

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Upload" subtitle="Add documents or website links, choose how to process them, then start." />
      <div className="grid gap-4 lg:grid-cols-[1fr,320px]">
        <div className="space-y-4">
          <div
            className={`flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-8 text-center ${drag ? "border-accent bg-accent/5" : "border-border"}`}
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDropFiles}
            onClick={() => input.current?.click()} role="button" tabIndex={0} aria-label="Add files"
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
          >
            <UploadCloud aria-hidden />
            <p className="font-medium">Drop files here or click to browse</p>
            <p className="text-xs text-muted">Up to {config.limits.max_file_mb} MB per file{config.limits.accepted_types.length ? ` · ${config.limits.accepted_types.join(", ")}` : ""}</p>
            <input ref={input} type="file" multiple hidden onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
          </div>

          <section className="card card-pad space-y-3">
            <h2 className="flex items-center gap-2 font-semibold"><Link2 size={16} aria-hidden /> Website links</h2>
            <Field label="One link per line"><textarea className="input" rows={3} value={urlText} onChange={(e) => setUrlText(e.target.value)} /></Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Purpose (optional)"><input className="input" value={purpose} onChange={(e) => setPurpose(e.target.value)} /></Field>
              <Field label="Authorization basis (optional)"><input className="input" value={basis} onChange={(e) => setBasis(e.target.value)} /></Field>
            </div>
            <button className="btn" disabled={!urlText.trim() || addUrls.isPending} onClick={() => addUrls.mutate()}>Check and add links</button>
          </section>

          <section aria-label="Files and links">
            {items.length === 0 ? <p className="text-sm text-muted">Nothing added yet.</p> : (
              <ul className="space-y-2">
                {items.map((it) => (
                  <li key={it.key} className="card flex items-start gap-3 p-3" draggable onDragStart={() => setDragKey(it.key)} onDragOver={(e) => { e.preventDefault(); reorder(it.key); }} onDragEnd={() => setDragKey(null)}>
                    <GripVertical size={16} className="mt-1 shrink-0 cursor-grab text-muted" aria-label="Drag to reorder" />
                    <div className="min-w-0 flex-1 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate font-medium">{it.result?.sanitized_filename ?? it.name}</span>
                        {it.size !== undefined && <span className="text-muted">{formatBytes(it.size)}</span>}
                        {it.result && <span className="chip">{it.result.detected_mime}</span>}
                        {it.state === "checking" && <span className="chip">Checking...</span>}
                        {it.result?.status === "accepted" && <span className="chip"><CheckCircle2 size={12} aria-hidden /> Accepted</span>}
                        {(it.result?.status === "rejected" || it.localProblem || it.state === "failed") && <span className="chip"><XCircle size={12} aria-hidden /> Not added</span>}
                        {it.urlResult && <span className="chip">{it.urlResult.status}</span>}
                      </div>
                      {it.localProblem && <p className="mt-1 text-danger">{it.localProblem}</p>}
                      {it.result?.error && <p className="mt-1 text-danger"><span className="chip mr-1">{it.result.error.code}</span>{it.result.error.message}</p>}
                      {it.result?.duplicate_of && <p className="mt-1 text-muted">Duplicate of {it.result.duplicate_of}</p>}
                      {it.urlResult?.reason && <p className="mt-1 text-muted">{it.urlResult.reason}</p>}
                      {it.urlResult?.error && <p className="mt-1 text-danger"><span className="chip mr-1">{it.urlResult.error.code}</span>{it.urlResult.error.message}</p>}
                      {it.error !== undefined && <InlineError error={it.error} />}
                      {it.kind === "file" && it.file && !it.localProblem && (it.result?.status === "rejected" || it.state === "failed") && (
                        <div className="mt-2 flex gap-2">
                          <input className="input max-w-xs" type="password" placeholder="Password, if the file is protected" aria-label="File password" value={it.password ?? ""} onChange={(e) => patch(it.key, { password: e.target.value })} />
                          <button className="btn btn-sm" onClick={() => void validate(it.key, it.file!, it.password)}>Retry</button>
                        </div>
                      )}
                    </div>
                    <button className="btn btn-sm" aria-label={`Remove ${it.name}`} onClick={() => setItems((xs) => xs.filter((x) => x.key !== it.key))}><Trash2 size={13} /></button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <aside className="space-y-4">
          <section className="card card-pad space-y-3">
            <fieldset><legend className="label">Processing mode</legend>
              {config.processing_modes.length === 0 ? <p className="text-sm text-muted">The backend returned no processing modes.</p> : (
                <div className="space-y-1.5">{config.processing_modes.map((m) => (
                  <label key={m.id} className="flex cursor-pointer items-start gap-2 text-sm"><input type="radio" name="mode" className="mt-1" checked={mode === m.id} onChange={() => setMode(m.id)} />
                    <span><span className="font-medium">{m.label}</span>{m.description && <span className="block text-xs text-muted">{m.description}</span>}</span></label>
                ))}</div>)}
            </fieldset>
            <fieldset><legend className="label">Output formats</legend>
              <div className="flex flex-wrap gap-2">{config.output_formats.map((f) => (
                <label key={f.id} className="chip cursor-pointer"><input type="checkbox" className="mr-1" checked={formats.includes(f.id)} onChange={(e) => setFormats(e.target.checked ? [...formats, f.id] : formats.filter((x) => x !== f.id))} />{f.label}</label>
              ))}</div>
            </fieldset>
            <Field label="Case">
              <select className="input" value={caseId} onChange={(e) => setCaseId(e.target.value)}>
                <option value="">{casePlaceholder}</option>
                {cases.data?.map((c) => <option key={c.case_id} value={c.case_id}>{c.name}</option>)}
              </select>
            </Field>
            {!caseId && <>
              <Field label="New case name (optional)"><input className="input" placeholder="e.g. Project Orion review" autoComplete="off" value={newCase} onChange={(e) => setNewCase(e.target.value)} /></Field>
              <p className="-mt-2 text-xs text-muted">Choose an existing case or enter a name to create one. Leave blank to skip case grouping.</p>
            </>}
            <Field label="Analysis instruction (optional)"><textarea className="input" rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)} /></Field>
          </section>

          {Object.keys(flags).length > 0 && (
            <details className="card card-pad">
              <summary className="cursor-pointer text-sm font-semibold">Advanced options</summary>
              <div className="mt-3 space-y-2">{Object.entries(flags).map(([k, v]) => (
                <label key={k} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v} onChange={(e) => setFlags({ ...flags, [k]: e.target.checked })} />{humanize(k)}</label>
              ))}</div>
            </details>
          )}

          <button className="btn btn-primary w-full justify-center" disabled={sourceIds.length === 0 || !mode || start.isPending} onClick={() => start.mutate()}>
            Start{sourceIds.length ? ` with ${sourceIds.length} source${sourceIds.length === 1 ? "" : "s"}` : ""}
          </button>
          {start.isError && <InlineError error={start.error} />}
        </aside>
      </div>
    </div>
  );
}
