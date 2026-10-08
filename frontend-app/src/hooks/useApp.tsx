import { createContext, useContext, type ReactNode } from "react";
import type { AppConfig, Me } from "@/types/api";

interface AppCtx { config: AppConfig; me: Me; signOut: () => void }
const Ctx = createContext<AppCtx | null>(null);

export function AppProvider({ value, children }: { value: AppCtx; children: ReactNode }) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
export function useApp(): AppCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp must be used inside AppProvider");
  return v;
}
export const useConfig = (): AppConfig => useApp().config;
export const useMe = (): Me => useApp().me;
/** Capability checks come only from the backend's /auth/me capabilities array. */
export function useCan(): (cap: string) => boolean {
  const caps = useApp().me.capabilities;
  return (cap) => caps.includes(cap);
}
