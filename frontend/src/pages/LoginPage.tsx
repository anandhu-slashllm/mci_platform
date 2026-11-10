import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { api, setSession, type SessionResponse } from "../api";
import AuthShell from "../components/portal/AuthShell";
import { UberButton, UberInput } from "../components/ui/UberPrimitives";

function MatIcon({ name, className = "" }: { name: string; className?: string }) {
  return <span className={`material-symbols-outlined ${className}`.trim()} aria-hidden>{name}</span>;
}

function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function LoginPage() {
  const { t, i18n } = useTranslation();
  const nav = useNavigate();
  const [mode, setMode] = useState<"worker" | "client" | "staff">("worker");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [otpStep, setOtpStep] = useState<"email" | "code">("email");
  const [devHint, setDevHint] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const inputClass =
    "w-full rounded-card border border-black bg-white px-4 py-3 text-black placeholder:text-[#afafaf] focus:outline-none focus:ring-2 focus:ring-black";

  async function submit() {
    setLoading(true);
    setMsg(null);
    try {
      const r = await api<SessionResponse>("/auth/login", {
        method: "POST",
        json: { username: username.trim(), password },
      });
      setSession(r);
      if (r.role === "worker") nav("/worker");
      else if (r.role === "client") nav("/client");
      else nav("/ta");
    } catch (e) {
      setMsg(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  async function sendOtp() {
    setMsg(null);
    setDevHint(null);
    if (!email.trim()) {
      setMsg("Enter your email address.");
      return;
    }
    setLoading(true);
    try {
      const locale = i18n.language.startsWith("zh") ? "zh" : "en";
      const endpoint = mode === "client" ? "/auth/client/email-otp/request" : "/auth/worker/email-otp/request";
      const r = await api<{ dev_code?: string | null }>(endpoint, {
        method: "POST",
        json: { email: email.trim(), locale, login_only: true },
      });
      if (r.dev_code) setDevHint(`Dev code: ${r.dev_code}`);
      setOtpStep("code");
    } catch (e) {
      setMsg(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  async function verifyOtp() {
    setMsg(null);
    if (!code.trim()) {
      setMsg("Enter the verification code.");
      return;
    }
    setLoading(true);
    try {
      const locale = i18n.language.startsWith("zh") ? "zh" : "en";
      const endpoint = mode === "client" ? "/auth/client/email-otp/verify" : "/auth/worker/email-otp/verify";
      const r = await api<SessionResponse>(endpoint, {
        method: "POST",
        json: { email: email.trim(), code: code.trim(), locale, login_only: true },
      });
      setSession(r);
      if (r.role === "worker") nav("/worker");
      else nav("/client");
    } catch (e) {
      setMsg(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell eyebrow={t("login_eyebrow")} title={t("login")} description={t("login_username_password_hint")}>
      <div className="rounded-featured border border-black/10 bg-white p-6 shadow-card md:p-8">
        <div className="mb-4 grid grid-cols-3 gap-2 rounded-pill bg-[#efefef] p-1">
          {[
            { id: "worker", label: "Worker" },
            { id: "client", label: "Client" },
            { id: "staff", label: "TA/Admin" },
          ].map((m) => (
            <button
              key={m.id}
              type="button"
              className={`rounded-pill px-2 py-2 text-xs font-semibold ${mode === m.id ? "bg-black text-white" : "text-[#4b4b4b]"}`}
              onClick={() => {
                setMode(m.id as "worker" | "client" | "staff");
                setMsg(null);
                setOtpStep("email");
                setCode("");
                setDevHint(null);
              }}
            >
              {m.label}
            </button>
          ))}
        </div>
        <div className="space-y-4">
          {mode === "staff" ? (
            <>
              <div>
                <label className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[#4b4b4b]">
                  <MatIcon name="person" className="text-base text-black" />
                  {t("username")}
                </label>
                <input
                  className={inputClass}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                />
              </div>
              <div>
                <label className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[#4b4b4b]">
                  <MatIcon name="lock" className="text-base text-black" />
                  {t("password")}
                </label>
                <input
                  type="password"
                  className={inputClass}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                />
              </div>
              <UberButton className="w-full text-sm font-bold disabled:opacity-50" onClick={() => void submit()} disabled={loading}>
                {loading ? "…" : t("login")}
              </UberButton>
            </>
          ) : (
            <>
              {otpStep === "email" ? (
                <>
                  <div>
                    <label className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[#4b4b4b]">
                      <MatIcon name="mail" className="text-base text-black" />
                      Email
                    </label>
                    <UberInput type="email" className={inputClass} value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
                  </div>
                  <UberButton className="w-full text-sm font-bold disabled:opacity-50" onClick={() => void sendOtp()} disabled={loading}>
                    {loading ? "…" : "Send verification code"}
                  </UberButton>
                </>
              ) : (
                <>
                  <p className="text-sm text-[#4b4b4b]">
                    Verification code sent to <span className="font-semibold text-black">{email.trim()}</span>
                  </p>
                  {devHint ? <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">{devHint}</p> : null}
                  <input
                    className={`${inputClass} text-center text-lg tracking-[0.35em]`}
                    value={code}
                    onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 10))}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    placeholder="••••••"
                  />
                  <UberButton className="w-full text-sm font-bold disabled:opacity-50" onClick={() => void verifyOtp()} disabled={loading}>
                    {loading ? "…" : "Verify and login"}
                  </UberButton>
                  <button
                    type="button"
                    className="w-full py-2 text-sm font-semibold text-black underline-offset-2 hover:underline"
                    onClick={() => {
                      setOtpStep("email");
                      setCode("");
                      setDevHint(null);
                    }}
                  >
                    Use a different email
                  </button>
                </>
              )}
            </>
          )}
          {msg && (
            <p className="whitespace-pre-wrap rounded-card border border-black/20 bg-[#efefef] px-3 py-2 text-sm text-black">
              {msg}
            </p>
          )}
        </div>

        <div className="mt-8 flex flex-col gap-3 border-t border-black/10 pt-6">
          <Link
            to="/worker/signup"
            className="flex w-full items-center justify-center gap-2 rounded-pill border border-black/20 bg-[#efefef] py-3 text-sm font-bold text-black transition-colors hover:bg-[#e2e2e2]"
          >
            <MatIcon name="person_add" className="text-lg" />
            {t("login_register")}
          </Link>
          <p className="text-center text-xs text-[#4b4b4b]">{t("login_register_hint")}</p>
          <Link
            to="/client/signup"
            className="flex w-full items-center justify-center gap-2 rounded-pill border border-black/20 bg-white py-3 text-sm font-bold text-black transition-colors hover:bg-[#efefef]"
          >
            <MatIcon name="business" className="text-lg" />
            {t("login_register_client")}
          </Link>
          <p className="text-center text-xs text-[#4b4b4b]">{t("login_register_client_hint")}</p>
        </div>
      </div>
    </AuthShell>
  );
}
