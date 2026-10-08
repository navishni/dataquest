// Single place that talks to the backend. Unwraps the envelope, validates with zod, maps failures to typed errors.
import { z } from "zod";
import { ApiRequestError, ContractError, NotConnectedError } from "./errors";
import { apiErrorSchema, type Schema } from "@/types/schemas";

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

let bearerToken: string | null = null; // memory only, never persisted
let credentialsMode: RequestCredentials = "same-origin";
let onUnauthorized: (() => void) | null = null;

// Connection indicator: reflects the outcome of the most recent request (true = backend answered, false = unreachable).
let connected: boolean | null = null;
const listeners = new Set<() => void>();
function setConnected(v: boolean): void {
  if (connected !== v) { connected = v; listeners.forEach((l) => l()); }
}
export const connectionStore = {
  subscribe(l: () => void): () => void { listeners.add(l); return () => { listeners.delete(l); }; },
  get(): boolean | null { return connected; },
};

export function setBearerToken(t: string | null): void {
  bearerToken = t;
}
export function hasBearerToken(): boolean {
  return bearerToken !== null;
}
/** Called once /config is known: cookie auth sends credentials, bearer auth sends the in-memory token. */
export function setAuthMode(mode: string): void {
  credentialsMode = mode === "cookie" ? "include" : "same-origin";
}
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

export function apiBase(): string {
  return (import.meta.env.VITE_API_BASE_URL ?? "").trim().replace(/\/+$/, "");
}
export function isBackendConfigured(): boolean {
  return apiBase().length > 0;
}
/** Resolve a backend-returned URL (absolute or relative) without ever exposing filesystem paths. */
export function resolveUrl(u: string): string {
  if (/^https?:\/\//i.test(u)) return u;
  return apiBase() + (u.startsWith("/") ? u : "/" + u);
}

export interface RequestOptions<T> {
  method?: Method;
  query?: Record<string, string | number | boolean | undefined | null>;
  json?: unknown;
  form?: FormData;
  schema?: Schema<T>;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions<unknown>["query"]): string {
  const url = new URL(resolveUrl(path), window.location.origin);
  if (query) for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  return url.toString();
}

function headers(json: boolean): Headers {
  const h = new Headers({ Accept: "application/json" });
  if (json) h.set("Content-Type", "application/json");
  if (bearerToken) h.set("Authorization", `Bearer ${bearerToken}`);
  return h;
}

const envelopeSchema = z.object({ ok: z.boolean(), request_id: z.string().optional() }).passthrough();

export interface Enveloped<T> { data: T; status: number; requestId?: string }

export async function apiRequestRaw<T>(path: string, opts: RequestOptions<T> = {}): Promise<Enveloped<T>> {
  if (!isBackendConfigured()) throw new NotConnectedError(path, "VITE_API_BASE_URL is not set");
  const method = opts.method ?? "GET";
  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method,
      headers: headers(opts.json !== undefined),
      body: opts.form ?? (opts.json !== undefined ? JSON.stringify(opts.json) : undefined),
      credentials: credentialsMode,
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    setConnected(false);
    throw new NotConnectedError(path, "network error");
  }
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw new NotConnectedError(path, `HTTP ${res.status}, response was not an API envelope`);
  }
  const env = envelopeSchema.safeParse(body);
  if (!env.success) throw new NotConnectedError(path, `HTTP ${res.status}, response was not an API envelope`);
  setConnected(true);
  const requestId = env.data.request_id;
  if (!env.data.ok) {
    const err = apiErrorSchema.safeParse((env.data as { error?: unknown }).error);
    const apiErr = err.success ? err.data : { code: "UNKNOWN_ERROR", message: `Request failed with HTTP ${res.status}` };
    if (res.status === 401 || (res.status === 403 && apiErr.code === "FORBIDDEN" && /authentication required|token/i.test(apiErr.message))) {
      onUnauthorized?.();
    }
    throw new ApiRequestError(apiErr, res.status, requestId);
  }
  const raw = (env.data as { data?: unknown }).data;
  if (opts.schema) {
    const parsed = opts.schema.safeParse(raw);
    if (!parsed.success) throw new ContractError(path, parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).slice(0, 8));
    return { data: parsed.data, status: res.status, requestId };
  }
  return { data: raw as T, status: res.status, requestId };
}

export async function apiRequest<T>(path: string, opts: RequestOptions<T> = {}): Promise<T> {
  return (await apiRequestRaw<T>(path, opts)).data;
}

/** Fetch a binary asset (page image, crop) with the same credentials; returns an object URL the caller must revoke. */
export async function fetchBlobUrl(url: string, signal?: AbortSignal): Promise<string> {
  if (!isBackendConfigured()) throw new NotConnectedError(url, "VITE_API_BASE_URL is not set");
  let res: Response;
  try {
    res = await fetch(resolveUrl(url), { headers: headers(false), credentials: credentialsMode, signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new NotConnectedError(url, "network error");
  }
  if (!res.ok) throw new NotConnectedError(url, `HTTP ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

/** Authenticated download of a backend-provided link (export, manifest). */
export async function downloadFromUrl(url: string, suggestedName: string): Promise<void> {
  const objectUrl = await fetchBlobUrl(url);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = suggestedName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000);
}
