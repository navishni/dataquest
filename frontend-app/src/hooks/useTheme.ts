import { useCallback, useEffect, useState } from "react";

// UI preferences only (theme, density) may use browser storage.
type Theme = "light" | "dark";
type Density = "comfortable" | "compact";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, v: string): void {
  try {
    localStorage.setItem(key, v);
  } catch {
    // storage unavailable: preference is session-only
  }
}

export function usePreferences() {
  const [theme, setThemeState] = useState<Theme>(() =>
    read("pf.theme") === "dark" || (read("pf.theme") === null && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light");
  const [density, setDensityState] = useState<Density>(() => (read("pf.density") === "compact" ? "compact" : "comfortable"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.classList.toggle("compact", density === "compact");
  }, [theme, density]);
  const setTheme = useCallback((t: Theme) => { setThemeState(t); write("pf.theme", t); }, []);
  const setDensity = useCallback((d: Density) => { setDensityState(d); write("pf.density", d); }, []);
  return { theme, setTheme, density, setDensity };
}
