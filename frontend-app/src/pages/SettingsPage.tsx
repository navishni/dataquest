import { PageHeader, KeyValue } from "@/components/common/Primitives";
import { usePreferences } from "@/hooks/useTheme";
import { useConfig, useMe } from "@/hooks/useApp";

export function SettingsPage() {
  const { theme, setTheme, density, setDensity } = usePreferences();
  const cfg = useConfig();
  const me = useMe();
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title="Settings" />
      <section className="card card-pad space-y-3"><h2 className="font-semibold">Appearance</h2>
        <label className="block text-sm">Theme <select className="input ml-2" value={theme} onChange={(e) => setTheme(e.target.value as "light" | "dark")}><option value="light">Light</option><option value="dark">Dark</option></select></label>
        <label className="block text-sm">Density <select className="input ml-2" value={density} onChange={(e) => setDensity(e.target.value as "comfortable" | "compact")}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label></section>
      <section className="card card-pad"><h2 className="mb-2 font-semibold">Account</h2>
        <KeyValue items={[{ k: "Name", v: me.display_name }, { k: "Role", v: me.role }, { k: "Capabilities", v: me.capabilities.join(", ") || "None" }]} /></section>
      <section className="card card-pad"><h2 className="mb-2 font-semibold">Feature flags</h2>
        {Object.keys(cfg.feature_flags).length === 0 ? <p className="text-sm text-muted">The backend returned no feature flags.</p> :
          <ul className="text-sm">{Object.entries(cfg.feature_flags).map(([k, v]) => <li key={k}>{k}: {v ? "on" : "off"}</li>)}</ul>}</section>
    </div>
  );
}
