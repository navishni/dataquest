import type { ApiError } from "@/types/canonical";

/** A well-formed failure envelope returned by the backend. */
export class ApiRequestError extends Error {
  readonly code: string;
  readonly details?: Record<string, unknown>;
  readonly requestId?: string;
  readonly status: number;
  constructor(err: ApiError, status: number, requestId?: string) {
    super(err.message);
    this.name = "ApiRequestError";
    this.code = err.code;
    this.details = err.details;
    this.requestId = requestId;
    this.status = status;
  }
}

/** Network failure, or a route that does not answer with an envelope (missing endpoint). Never replaced by invented data. */
export class NotConnectedError extends Error {
  readonly endpoint: string;
  constructor(endpoint: string, reason?: string) {
    super(reason ? `Backend not connected: ${endpoint} (${reason})` : `Backend not connected: ${endpoint}`);
    this.name = "NotConnectedError";
    this.endpoint = endpoint;
  }
}

/** The response did not match the documented contract. */
export class ContractError extends Error {
  readonly endpoint: string;
  readonly issues: string[];
  constructor(endpoint: string, issues: string[]) {
    super(`Response from ${endpoint} did not match the documented contract`);
    this.name = "ContractError";
    this.endpoint = endpoint;
    this.issues = issues;
  }
}

/** True when the backend is telling us the caller is not signed in (as opposed to lacking a capability). */
export function isAuthError(e: unknown): boolean {
  if (!(e instanceof ApiRequestError)) return false;
  if (e.status === 401) return true;
  return e.status === 403 && /authenticat|token|expired|sign.?in/i.test(e.message);
}
