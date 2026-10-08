import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, Eye, LockKeyhole, PencilLine, ShieldCheck, Sparkles } from "lucide-react";
import { login, ENDPOINTS, type LoginRole } from "@/api/platform";
import { setBearerToken } from "@/api/client";
import { Field } from "@/components/common/Primitives";
import { InlineError } from "@/components/common/StateViews";
import type { AppConfig } from "@/types/api";

const ROLES: { id: LoginRole; label: string; description: string; Icon: typeof ShieldCheck }[] = [
  { id: "admin", label: "Admin", description: "Manage access", Icon: ShieldCheck },
  { id: "editor", label: "Editor", description: "Prepare and review", Icon: PencilLine },
  { id: "viewer", label: "Viewer", description: "View shared data", Icon: Eye },
];

export function LoginScreen({ config, onSignedIn }: { config: AppConfig | null; onSignedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<LoginRole>("viewer");
  const loginPath = config?.auth.login_url ?? ENDPOINTS.login;
  const m = useMutation({
    mutationFn: () => login(loginPath, { username: username.trim(), password, role }),
    onSuccess: (r) => { setBearerToken(r.access_token); onSignedIn(); },
  });

  return (
    <main className="login-page flex min-h-full items-center justify-center px-4 py-8 sm:px-6">
      <div className="grid w-full max-w-6xl overflow-hidden rounded-3xl border border-border bg-surface shadow-2xl lg:min-h-[690px] lg:grid-cols-[1fr,0.92fr]">
        <section className="login-art relative hidden flex-col justify-between overflow-hidden p-10 lg:flex xl:p-14" aria-label="About ParseFusion">
          <div className="relative z-10 flex items-center gap-3">
            <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-accent text-accent-fg shadow-lg"><Sparkles size={21} aria-hidden /></span>
            <div><p className="text-lg font-bold tracking-tight">ParseFusion</p><p className="text-xs text-muted">Document intelligence workspace</p></div>
          </div>
          <div className="relative z-10 max-w-lg pb-8">
            <p className="mb-4 inline-flex items-center gap-2 rounded-full border border-border bg-surface/70 px-3 py-1.5 text-xs font-medium text-muted"><LockKeyhole size={13} aria-hidden /> Secure, role-based access</p>
            <h1 className="text-4xl font-semibold leading-tight tracking-tight xl:text-5xl">Make complex documents easier to understand.</h1>
            <p className="mt-5 max-w-md text-base leading-7 text-muted">Review extracted information, follow evidence back to its source, and share only the data each person is allowed to see.</p>
          </div>
          <p className="relative z-10 text-xs text-muted">One clear workspace for documents, cases, and controlled access.</p>
          <div className="login-glow pointer-events-none absolute -right-20 -top-20 h-80 w-80 rounded-full" />
          <div className="login-glow login-glow-secondary pointer-events-none absolute -bottom-24 -left-16 h-72 w-72 rounded-full" />
        </section>

        <section className="flex items-center justify-center p-6 sm:p-10 lg:p-12">
          <div className="w-full max-w-md">
            <div className="mb-8 lg:hidden">
              <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-2xl bg-accent text-accent-fg"><Sparkles size={21} aria-hidden /></div>
              <p className="text-xl font-bold tracking-tight">ParseFusion</p>
              <p className="mt-1 text-sm text-muted">Document intelligence workspace</p>
            </div>
            <div className="mb-7">
              <p className="text-sm font-medium text-accent">Welcome back</p>
              <h2 className="mt-1 text-3xl font-semibold tracking-tight">Sign in</h2>
              <p className="mt-2 text-sm leading-6 text-muted">Choose the role assigned to your account, then enter your credentials.</p>
            </div>

            <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
              <fieldset>
                <legend className="label mb-2">Sign in as</legend>
                <div className="grid grid-cols-3 gap-2">
                  {ROLES.map(({ id, label, description, Icon }) => {
                    const selected = role === id;
                    return (
                      <label key={id} className={`relative flex min-h-[90px] cursor-pointer flex-col items-start justify-center gap-2 rounded-xl border px-3 py-3 text-left transition-colors focus-within:ring-2 focus-within:ring-accent ${selected ? "border-accent bg-accent/10 ring-1 ring-accent" : "border-border bg-surface hover:bg-surface2"}`}>
                        <input className="peer sr-only" type="radio" name="account-role" value={id} checked={selected} onChange={() => setRole(id)} />
                        <Icon size={17} className={selected ? "text-accent" : "text-muted"} aria-hidden />
                        <span><span className="block text-sm font-semibold">{label}</span><span className="mt-0.5 block text-[11px] leading-4 text-muted">{description}</span></span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              <div className="space-y-4">
                <Field label="Username"><input className="input h-11" autoComplete="username" autoCapitalize="none" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} /></Field>
                <Field label="Password"><input className="input h-11" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
              </div>

              {m.isError && <InlineError error={m.error} />}
              <button className="btn btn-primary h-11 w-full justify-center rounded-lg text-sm" disabled={!username.trim() || !password || m.isPending}>
                {m.isPending ? "Signing in…" : "Continue"}{!m.isPending && <ArrowRight size={16} aria-hidden />}
              </button>
            </form>
            <p className="mt-5 text-center text-xs leading-5 text-muted">Your selected role is checked against your account. It does not change your assigned permissions.</p>
            <p className="mt-3 text-center text-[11px] text-muted">Your session stays in memory and clears when this tab is closed or refreshed.</p>
          </div>
        </section>
      </div>
    </main>
  );
}
