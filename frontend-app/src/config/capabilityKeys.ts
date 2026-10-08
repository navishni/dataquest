// Capability KEY NAMES the UI checks against GET /auth/me `capabilities`. This is the only place those names live.
// The backend team can edit the strings to match their capability vocabulary. There is deliberately NO role -> capability map.
export const CAP = {
  upload: "upload",
  view: "view",
  audit: "audit",
  approve: "approve",
  execute: "execute",
  export: "export",
  unmask: "unmask",
  admin: "admin",
  chat: "chat",
  manageAccess: "manage_access",
  requestAccess: "request_access",
  analysis: "analysis",
  draftAction: "draft_action",
} as const;
export type CapKey = (typeof CAP)[keyof typeof CAP];

/** Capability required to attempt each approval decision. The backend still validates every transition. */
export const DECISION_CAP: Record<string, CapKey> = {
  save_edit: CAP.view,
  submit_review: CAP.view,
  cancel: CAP.view,
  approve: CAP.approve,
  reject: CAP.approve,
  execute: CAP.execute,
};
