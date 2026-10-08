// Navigation entries and the capability key each one requires (key names live in capabilityKeys.ts).
import { CAP, type CapKey } from "./capabilityKeys";

export interface NavEntry { to: string; label: string; icon: "upload" | "batches" | "cases" | "chat" | "access" | "exports" | "audit" | "metrics" | "agents" | "settings"; cap: CapKey | null; group: "Workspace" | "Review" | "System" }

export const NAV: NavEntry[] = [
  { to: "/", label: "Upload", icon: "upload", cap: CAP.upload, group: "Workspace" },
  { to: "/batches", label: "Batches", icon: "batches", cap: CAP.view, group: "Workspace" },
  { to: "/cases", label: "Cases", icon: "cases", cap: CAP.view, group: "Workspace" },
  { to: "/chat", label: "Chat", icon: "chat", cap: CAP.chat, group: "Review" },
  { to: "/access", label: "Access and visibility", icon: "access", cap: CAP.view, group: "Review" },
  { to: "/exports", label: "Exports", icon: "exports", cap: CAP.export, group: "Review" },
  { to: "/audit", label: "Audit log", icon: "audit", cap: CAP.audit, group: "System" },
  { to: "/metrics", label: "Quality and metrics", icon: "metrics", cap: CAP.admin, group: "System" },
  { to: "/agents", label: "Agent status", icon: "agents", cap: CAP.view, group: "System" },
  { to: "/settings", label: "Settings", icon: "settings", cap: null, group: "System" },
];
