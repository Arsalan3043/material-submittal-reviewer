"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { login } from "@/lib/auth";
import { Button, MonoLabel, TextInput } from "@/components/ui";

/** README Screen 1 — full-bleed two-pane split on laptop, single column on mobile. */
export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionExpired = searchParams.get("reason") === "session-expired";
  const next = searchParams.get("next");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function validate(): string | null {
    if (!email.includes("@")) return "Enter the work email your admin provisioned.";
    if (password.length < 4) return "Password must be at least 4 characters.";
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      router.replace(next && next.startsWith("/") ? next : "/projects");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") handleSubmit(e as unknown as React.FormEvent);
  }

  return (
    <div className="flex flex-1 flex-col md:flex-row">
      {/* Left pane — laptop only */}
      <div className="hidden flex-col justify-between bg-ink p-[52px] md:flex md:w-[52%]">
        <div className="flex items-center gap-2.5">
          <div className="h-6 w-6 rounded-[7px] bg-accent" />
          <span className="text-base font-semibold text-[#FAFAF9]">Clause</span>
        </div>
        <div className="flex max-w-[420px] flex-col gap-4.5">
          <h1 className="text-[34px] font-semibold leading-[1.2] tracking-[-0.02em] text-[#FAFAF9] text-pretty">
            Material submittals, reviewed against ADM and TAQA clauses in under five minutes.
          </h1>
          <p className="max-w-[400px] text-sm leading-relaxed text-[#9A9A9F]">
            Every finding carries the spec clause and the page it came from. If we can&rsquo;t
            cite it, we don&rsquo;t say it.
          </p>
        </div>
        <div className="flex gap-6.5">
          <div>
            <div className="text-xl font-semibold text-[#FAFAF9]">4m 12s</div>
            <div className="mt-1 font-mono text-[11.5px] text-[#7E7E85]">AVG FIRST PASS</div>
          </div>
          <div>
            <div className="text-xl font-semibold text-[#FAFAF9]">10 yrs</div>
            <div className="mt-1 font-mono text-[11.5px] text-[#7E7E85]">RETENTION READY</div>
          </div>
        </div>
      </div>

      {/* Right pane / mobile single column */}
      <div className="flex flex-1 items-center justify-center bg-canvas p-6 md:p-10">
        <div className="flex w-full max-w-[352px] flex-col gap-4">
          {/* Mobile-only logo + headline (laptop shows the left pane instead) */}
          <div className="flex flex-col gap-4 md:hidden">
            <div className="h-[30px] w-[30px] rounded-[9px] bg-accent" />
            <h1 className="text-[26px] font-semibold leading-[1.2] tracking-[-0.02em] text-ink">
              Sign in to Clause
            </h1>
            <p className="text-[13.5px] leading-relaxed text-[#7C7F84]">
              Provisioned accounts only. Use the credentials from your tenant admin.
            </p>
          </div>

          <div className="hidden md:block">
            <h1 className="text-[22px] font-semibold tracking-[-0.01em] text-ink">Sign in</h1>
            <p className="mt-1.5 text-[13px] leading-relaxed text-[#7C7F84]">
              Accounts are provisioned by your tenant admin — there&rsquo;s no sign-up to fill
              in.
            </p>
          </div>

          {sessionExpired && (
            <div className="rounded-[9px] bg-warning-bg px-3 py-2.5 text-[12.5px] text-warning-text">
              Your session expired — sign in again.
            </div>
          )}

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <MonoLabel htmlFor="email" className="hidden md:block">
                WORK EMAIL
              </MonoLabel>
              <TextInput
                id="email"
                type="email"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="you@company.ae"
                className="h-[52px] rounded-[13px] text-base md:h-[42px] md:rounded-[10px] md:text-sm"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <MonoLabel htmlFor="password" className="hidden md:block">
                PASSWORD
              </MonoLabel>
              <TextInput
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="••••••••"
                className="h-[52px] rounded-[13px] text-base md:h-[42px] md:rounded-[10px] md:text-sm"
              />
            </div>

            {error && (
              <div className="animate-popIn rounded-[9px] bg-critical-bg px-3 py-2.5 text-[12.5px] text-critical-text">
                {error}
              </div>
            )}

            <Button
              type="submit"
              disabled={submitting}
              className="h-[54px] rounded-[14px] text-base md:h-11 md:rounded-[10px] md:text-sm"
            >
              {submitting ? "Signing in…" : "Continue"}
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
