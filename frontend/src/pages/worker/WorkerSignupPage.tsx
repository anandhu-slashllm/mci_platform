import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { api, setSession, type SessionResponse } from "../../api";
import AuthShell from "../../components/portal/AuthShell";
import { UberButton } from "../../components/ui/UberPrimitives";

function MatIcon({ name, className = "" }: { name: string; className?: string }) {
  return <span className={`material-symbols-outlined ${className}`.trim()} aria-hidden>{name}</span>;
}

type OtpRequestRes = {
  ok: boolean;
  message?: string;
  dev_code?: string | null;
  /** "dev" = code read back from the local dev mailbox; "email" = sent by Supabase Auth */
  delivery?: "dev" | "email";
};

export default function WorkerSignupPage() {
  const { t, i18n } = useTranslation();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [devHint, setDevHint] = useState<string | null>(null);
  const [deliveryKind, setDeliveryKind] = useState<"dev" | "email" | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const inputClass =
    "w-full rounded-card border border-black bg-white px-4 py-3 text-black placeholder:text-[#afafaf] focus:outline-none focus:ring-2 focus:ring-black";

  async function sendCode() {
    setMsg(null);
    setDevHint(null);
    setDeliveryKind(null);
    const em = email.trim();
    if (!em) {
      setMsg(t("worker_signup_email_required"));
      return;
    }
    setLoading(true);
    try {
      const locale = i18n.language.startsWith("zh") ? "zh" : "en";
      const r = await api<OtpRequestRes>("/auth/worker/email-otp/request", {
        method: "POST",
        json: { email: em, locale },
      });
      setDeliveryKind(r.delivery ?? (r.dev_code ? "dev" : "email"));
      if (r.dev_code) setDevHint(t("worker_signup_email_dev_hint", { code: r.dev_code }));
      setStep("code");
    } catch (e) {
      setMsg(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function verify() {
    setMsg(null);
    const em = email.trim();
    if (!code.trim()) {
      setMsg(t("worker_signup_code_required"));
      return;
    }
    setLoading(true);
    try {
      const locale = i18n.language.startsWith("zh") ? "zh" : "en";
      const r = await api<SessionResponse>("/auth/worker/email-otp/verify", {
        method: "POST",
        json: { email: em, code: code.trim(), locale },
      });
      setSession(r);
      nav("/worker/onboarding", { replace: true });
    } catch (e) {
      setMsg(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      eyebrow={t("worker_signup_eyebrow")}
      title={t("worker_signup_title")}
      description={t("worker_signup_subtitle")}
    >
      <div className="rounded-featured border border-black/10 bg-white p-6 shadow-card md:p-8">
        {step === "email" && (
          <div className="space-y-4">
            <div>
              <label className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[#4b4b4b]">
                <MatIcon name="mail" className="text-base text-black" />
                {t("worker_signup_email_label")}
              </label>
              <input
                type="email"
                autoComplete="email"
                className={inputClass}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
              />
            </div>
            <UberButton disabled={loading} className="flex w-full items-center justify-center gap-2 text-sm font-bold disabled:opacity-50" onClick={() => void sendCode()}>
              <MatIcon name="send" className="text-lg" />
              {loading ? "…" : t("worker_signup_send_code")}
            </UberButton>
            <p className="text-center text-xs text-[#4b4b4b]">{t("worker_signup_phone_coming")}</p>
          </div>
        )}

        {step === "code" && (
          <div className="space-y-4">
            <p className="text-sm text-[#4b4b4b]">
              {t("worker_signup_sent_to")}{" "}
              <span className="font-semibold text-black">{email.trim()}</span>
            </p>
            {deliveryKind === "dev" ? (
              <p className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-950 dark:border-amber-800/60 dark:bg-amber-950/35 dark:text-amber-100">
                {t("worker_signup_delivery_dev_notice")}
              </p>
            ) : null}
            {deliveryKind === "email" ? (
              <p className="rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-950 dark:border-sky-800/50 dark:bg-sky-950/30 dark:text-sky-100">
                {t("worker_signup_delivery_resend_hint")}
              </p>
            ) : null}
            {devHint ? (
              <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950 dark:border-amber-800/50 dark:bg-amber-950/30 dark:text-amber-100">
                {devHint}
              </p>
            ) : null}
            <div>
              <label className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[#4b4b4b]">
                <MatIcon name="pin" className="text-base text-black" />
                {t("worker_signup_code_label")}
              </label>
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                className={`${inputClass} text-center text-lg tracking-[0.35em]`}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 10))}
                placeholder="••••••"
              />
            </div>
            <UberButton disabled={loading} className="flex w-full items-center justify-center gap-2 text-sm font-bold disabled:opacity-50" onClick={() => void verify()}>
              <MatIcon name="verified_user" className="text-lg" />
              {loading ? "…" : t("worker_signup_verify_continue")}
            </UberButton>
            <button
              type="button"
              className="w-full py-2 text-sm font-semibold text-black underline-offset-2 hover:underline"
              onClick={() => {
                setStep("email");
                setCode("");
                setDevHint(null);
                setDeliveryKind(null);
              }}
            >
              {t("worker_signup_change_email")}
            </button>
          </div>
        )}

        {msg ? (
          <p className="mt-4 whitespace-pre-wrap rounded-card border border-black/20 bg-[#efefef] px-3 py-2 text-sm text-black">
            {msg}
          </p>
        ) : null}

        <p className="mt-6 border-t border-black/10 pt-6 text-center text-[11px] leading-relaxed text-[#4b4b4b]">
          {t("worker_signup_resend_note")}
        </p>

        <div className="mt-4 text-center">
          <Link
            to="/login"
            className="inline-flex items-center gap-2 text-sm font-semibold text-black hover:text-[#4b4b4b]"
          >
            <MatIcon name="login" className="text-lg" />
            {t("worker_welcome_have_account")}
          </Link>
        </div>
      </div>
    </AuthShell>
  );
}
