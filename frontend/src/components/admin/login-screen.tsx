"use client";

import { useState } from "react";
import { LangSwitch } from "@/components/admin/lang-switch";
import { LogoMark } from "@/components/logo-mark";
import { LabelInput } from "@/components/spell/label-input";
import { Spinner } from "@/components/spell/spinner";
import { Button } from "@/components/ui/button";
import { signIn, takeExpiredFlag } from "@/lib/admin";
import { ADMIN_UI } from "@/lib/admin-i18n";
import { ApiRequestError } from "@/lib/api";
import { UI } from "@/lib/i18n";
import { useUILang } from "@/lib/lang";
import { useApiMode } from "@/lib/mode";

export function LoginScreen() {
  const lang = useUILang();
  const t = ADMIN_UI[lang];
  const mode = useApiMode();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(() => (takeExpiredFlag() ? t.sessionOver : null));

  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await signIn(String(form.get("login") ?? "").trim(), String(form.get("password") ?? ""));
    } catch (err) {
      const code = err instanceof ApiRequestError ? err.body.error : "unavailable";
      setError(
        code === "unauthorized" ? t.login.wrong
        : code === "rate_limited" ? t.login.tooMany
        : (UI[lang].errors[code] ?? UI[lang].errors.internal),
      );
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-6 px-4 py-10">
      <form
        className="flex w-full max-w-sm flex-col gap-5 rounded-3xl border bg-card p-8 shadow-[0_24px_60px_-30px_rgb(15_42_74/0.35)]"
        onSubmit={submit}
      >
        <div className="flex flex-col items-center gap-3 text-center">
          <LogoMark className="h-12 w-auto" />
          <div>
            <h1 className="font-semibold text-xl tracking-tight">{t.login.title}</h1>
            <p className="mt-1 text-muted-foreground text-sm">{t.login.subtitle}</p>
          </div>
        </div>
        <div className="flex flex-col gap-4 pt-1">
          <LabelInput autoComplete="username" autoFocus label={t.login.login} name="login" required ringColor="blue" type="text" />
          <LabelInput autoComplete="current-password" label={t.login.password} name="password" required ringColor="blue" type="password" />
        </div>
        {error && (
          <p className="rounded-lg bg-destructive/10 px-3 py-2 text-destructive text-sm" role="alert">
            {error}
          </p>
        )}
        <Button className="h-10 rounded-xl" disabled={busy} type="submit">
          {busy && <Spinner className="size-4" />}
          {t.login.submit}
        </Button>
        {mode === "mock" && <p className="text-center text-muted-foreground text-xs">{t.login.mockHint}</p>}
      </form>
      <LangSwitch />
    </main>
  );
}
