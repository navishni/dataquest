import { useSyncExternalStore } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  Activity, BarChart3, Boxes, ChevronRight, FolderKanban, History, Lock, LogOut, MessageSquare, Moon, Settings, Share2, Sparkles, Sun, Upload, Wifi, WifiOff,
} from "lucide-react";
import { useLocation } from "react-router-dom";
import { connectionStore } from "@/api/client";
import { NAV, type NavEntry } from "@/config/navigation";
import { useApp, useCan } from "@/hooks/useApp";
import { usePreferences } from "@/hooks/useTheme";

const ICONS: Record<NavEntry["icon"], typeof Upload> = {
  upload: Upload, batches: Boxes, cases: FolderKanban, chat: MessageSquare, access: Lock, exports: Share2, audit: History,
  metrics: BarChart3, agents: Activity, settings: Settings,
};

export function Layout() {
  const { me, signOut } = useApp();
  const can = useCan();
  const { theme, setTheme } = usePreferences();
  const connected = useSyncExternalStore(connectionStore.subscribe, connectionStore.get);
  const location = useLocation();
  const visibleNav = NAV.filter((n) => n.cap === null || can(n.cap));
  const currentPage = visibleNav.find((n) => n.to === "/" ? location.pathname === "/" : location.pathname === n.to || location.pathname.startsWith(`${n.to}/`));
  const groups = (["Workspace", "Review", "System"] as const)
    .map((group) => ({ group, entries: visibleNav.filter((n) => n.group === group) }))
    .filter((item) => item.entries.length > 0);
  return (
    <div className="flex h-full">
      <nav aria-label="Main" className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface p-4 md:flex">
        <NavLink to="/" className="mb-7 flex items-center gap-3 rounded-lg px-1 py-1 text-fg hover:opacity-90">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent text-accent-fg"><Sparkles size={19} aria-hidden /></span>
          <span><span className="block text-base font-bold tracking-tight">ParseFusion</span><span className="block text-[11px] text-muted">Document workspace</span></span>
        </NavLink>
        <div className="space-y-6">
          {groups.map(({ group, entries }) => (
            <section key={group} aria-label={`${group} navigation`}>
              <h2 className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">{group}</h2>
              <ul className="space-y-1">
                {entries.map((n) => {
                  const Icon = ICONS[n.icon];
                  return <li key={n.to}>
                    <NavLink to={n.to} end={n.to === "/"} className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${isActive ? "bg-accent/10 font-semibold text-accent" : "text-muted hover:bg-surface2 hover:text-fg"}`}>
                      <Icon size={16} aria-hidden />{n.label}
                    </NavLink>
                  </li>;
                })}
              </ul>
            </section>
          ))}
        </div>
        <div className="mt-auto rounded-xl border border-border bg-surface2/60 p-3">
          <p className="text-xs font-medium">Need something?</p>
          <p className="mt-1 text-[11px] leading-4 text-muted">Start with Upload, then follow batches and cases as documents are processed.</p>
        </div>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-14 items-center justify-between gap-3 border-b border-border bg-surface px-4 py-2.5">
          <details className="relative md:hidden">
            <summary className="btn btn-sm cursor-pointer list-none">Menu</summary>
            <ul className="card absolute z-30 mt-1 w-52 p-2">
              {groups.map(({ group, entries }) => <li key={group} className="mb-1 last:mb-0">
                <p className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted">{group}</p>
                {entries.map((n) => <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => `block rounded px-2 py-2 text-sm ${isActive ? "bg-accent/10 font-semibold text-accent" : "hover:bg-surface2"}`}>{n.label}</NavLink>)}
              </li>)}
            </ul>
          </details>
          <div className="hidden items-center gap-2 text-sm md:flex"><span className="text-muted">Workspace</span><ChevronRight size={14} className="text-muted" aria-hidden /><span className="font-medium">{currentPage?.label ?? "Documents"}</span></div>
          <div className="ml-auto flex items-center gap-2 text-sm sm:gap-3">
            <span className="chip" role="status" title={connected === false ? "The backend did not answer the last request" : "The backend answered the last request"}>
              {connected === false ? <WifiOff size={12} aria-hidden /> : <Wifi size={12} aria-hidden />}{connected === false ? "Backend not connected" : connected ? "Connected" : "Checking"}
            </span>
            <span className="hidden sm:inline"><span className="font-medium">{me.display_name}</span> <span className="text-muted">({me.role})</span></span>
            <button className="btn btn-sm" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
            </button>
            <button className="btn btn-sm" onClick={signOut}><LogOut size={14} /> Sign out</button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-auto p-4"><Outlet /></main>
      </div>
    </div>
  );
}
