import { useEffect, useState } from "react";
import { fetchBlobUrl } from "@/api/client";
import { NotConnectedError } from "@/api/errors";

export interface AuthedUrlState { url: string | null; loading: boolean; error: Error | null }

/** Loads a backend image (page, crop, screenshot) with the app's credentials and exposes an object URL. */
export function useAuthedUrl(src: string | undefined): AuthedUrlState {
  const [state, setState] = useState<AuthedUrlState>({ url: null, loading: !!src, error: null });
  useEffect(() => {
    if (!src) { setState({ url: null, loading: false, error: null }); return; }
    const ctl = new AbortController();
    let created: string | null = null;
    setState({ url: null, loading: true, error: null });
    fetchBlobUrl(src, ctl.signal)
      .then((u) => { created = u; setState({ url: u, loading: false, error: null }); })
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setState({ url: null, loading: false, error: e instanceof Error ? e : new NotConnectedError(src) });
      });
    return () => { ctl.abort(); if (created) URL.revokeObjectURL(created); };
  }, [src]);
  return state;
}
