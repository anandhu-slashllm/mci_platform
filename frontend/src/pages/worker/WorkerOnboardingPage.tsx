import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { api, apiUrl, getToken, getValidToken } from "../../api";
import { CertificationMultiPicker } from "../../components/certifications/CertificationMultiPicker";
import { JobTypeMultiPicker } from "../../components/jobTypes/JobTypeMultiPicker";
import { DEFAULT_MAP_CENTER, LocationMapPicker } from "../../components/maps/LocationMapPicker";
import { SkillMultiPicker } from "../../components/skills/SkillMultiPicker";
import { isAllowedJobType } from "../../constants/jobTypes";
import {
  computeOnboardingProgress,
  progressHint,
  type OnboardingDraft,
} from "../../lib/onboardingProgress";

const DRAFT_KEY = "mci_onboarding_draft_v1";

type Profile = {
  full_name: string;
  nric_masked: string | null;
  phone: string;
  email: string | null;
  languages: string[];
  job_type_preferences: string[];
  skills: string[];
  certifications: string[];
  availability: Record<string, unknown>;
  location_lat: number | null;
  location_lng: number | null;
  bank_name: string | null;
  work_pass_not_applicable: boolean;
  profile_completeness_pct: number;
  onboarding_completed: boolean;
};

const DOC_SLOTS = ["nric_copy", "work_pass", "certification", "training_record"] as const;
const LANGUAGE_OPTIONS = [
  "English",
  "Mandarin",
  "Chinese",
  "Malay",
  "Tamil",
  "Hindi",
  "Urdu",
  "Bengali",
  "Punjabi",
  "Marathi",
  "Telugu",
  "Kannada",
  "Gujarati",
  "Thai",
  "Vietnamese",
  "Indonesian",
  "Korean",
  "Japanese",
  "Arabic",
  "French",
  "Spanish",
  "German",
  "Italian",
  "Portuguese",
  "Russian",
  "Turkish",
  "Dutch",
  "Greek",
  "Swedish",
  "Polish",
] as const;

function maskBank(s: string): string {
  const d = s.replace(/\s/g, "");
  if (d.length <= 4) return "••••";
  return "••••••" + d.slice(-4);
}

function canonicalizeLanguage(raw: string): string | null {
  const v = raw.trim();
  if (!v) return null;
  const lower = v.toLowerCase();
  const found = LANGUAGE_OPTIONS.find((l) => l.toLowerCase() === lower);
  return found ?? null;
}

export default function WorkerOnboardingPage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [step, setStep] = useState(1);
  const [fullName, setFullName] = useState("");
  const [nric, setNric] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [langs, setLangs] = useState<string[]>([]);
  const [langInput, setLangInput] = useState("");
  const [jobTypes, setJobTypes] = useState<string[]>([]);
  const [skills, setSkills] = useState<string[]>([]);
  const [certs, setCerts] = useState<string[]>([]);
  const [availMorning, setAvailMorning] = useState(false);
  const [availEvening, setAvailEvening] = useState(false);
  const [availWeekend, setAvailWeekend] = useState(false);
  const [bankAccount, setBankAccount] = useState("");
  const [bankName, setBankName] = useState("");
  const [workPassNa, setWorkPassNa] = useState(false);
  const [mapLat, setMapLat] = useState(DEFAULT_MAP_CENTER.lat);
  const [mapLng, setMapLng] = useState(DEFAULT_MAP_CENTER.lng);
  const [docTypes, setDocTypes] = useState<Set<string>>(new Set());
  const [uploadPct, setUploadPct] = useState<Record<string, number>>({});
  const [terms, setTerms] = useState(false);
  const [pdpa, setPdpa] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [mapGeoMsg, setMapGeoMsg] = useState<string | null>(null);
  const [serverPct, setServerPct] = useState<number | null>(null);
  const [uploadedDocNames, setUploadedDocNames] = useState<Record<string, string | null>>({});
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const availability = useMemo(
    () => ({
      morning: availMorning,
      evening: availEvening,
      weekend: availWeekend,
    }),
    [availMorning, availEvening, availWeekend],
  );

  const draft: OnboardingDraft = useMemo(
    () => ({
      fullName,
      nric,
      phone,
      email,
      languages: langs,
      jobTypes,
      availability,
      skills,
      certifications: certs,
      bankAccount,
      bankName,
      workPassNa,
      locationLat: mapLat,
      locationLng: mapLng,
      docTypes,
    }),
    [
      fullName,
      nric,
      phone,
      email,
      langs,
      jobTypes,
      availability,
      skills,
      certs,
      bankAccount,
      bankName,
      workPassNa,
      mapLat,
      mapLng,
      docTypes,
    ],
  );

  const uiPct = computeOnboardingProgress(draft);
  const pct = serverPct ?? uiPct;

  const languageSuggestions = useMemo(() => {
    const q = langInput.trim().toLowerCase();
    return LANGUAGE_OPTIONS.filter((l) => {
      if (langs.includes(l)) return false;
      if (!q) return true;
      return l.toLowerCase().includes(q);
    }).slice(0, 8);
  }, [langInput, langs]);

  const persistDraftLocal = useCallback(() => {
    try {
      localStorage.setItem(
        DRAFT_KEY,
        JSON.stringify({
          step,
          fullName,
          nric,
          phone,
          email,
          langs,
          jobTypes,
          skills,
          certs,
          availMorning,
          availEvening,
          availWeekend,
          bankAccount,
          bankName,
          workPassNa,
          mapLat,
          mapLng,
        }),
      );
    } catch {
      /* ignore */
    }
  }, [
    step,
    fullName,
    nric,
    phone,
    email,
    langs,
    jobTypes,
    skills,
    certs,
    availMorning,
    availEvening,
    availWeekend,
    bankAccount,
    bankName,
    workPassNa,
    mapLat,
    mapLng,
  ]);

  const saveRemote = useCallback(async () => {
    if (!getToken()) return;
    try {
      const p = await api<Profile>("/workers/me/profile", {
        method: "PUT",
        json: {
          full_name: fullName,
          nric: nric || undefined,
          email: email || undefined,
          phone: phone || undefined,
          languages: langs,
          job_type_preferences: jobTypes,
          skills,
          certifications: certs,
          availability,
          bank_account: bankAccount || undefined,
          bank_name: bankName || undefined,
          work_pass_not_applicable: workPassNa,
          location_lat: mapLat,
          location_lng: mapLng,
        },
      });
      setServerPct(p.profile_completeness_pct);
    } catch {
      /* silent autosave */
    }
  }, [
    fullName,
    nric,
    email,
    langs,
    jobTypes,
    skills,
    certs,
    availability,
    bankAccount,
    bankName,
    workPassNa,
    mapLat,
    mapLng,
  ]);

  useEffect(() => {
    persistDraftLocal();
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveRemote();
    }, 800);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [persistDraftLocal, saveRemote]);

  useEffect(() => {
    (async () => {
      try {
        let mapFromDraft = false;
        const raw = localStorage.getItem(DRAFT_KEY);
        if (raw) {
          const d = JSON.parse(raw);
          if (d.step) setStep(d.step);
          if (d.fullName != null) setFullName(d.fullName);
          if (d.nric != null) setNric(d.nric);
          if (d.phone != null) setPhone(d.phone);
          if (d.email != null) setEmail(d.email);
          if (Array.isArray(d.langs)) setLangs(d.langs);
          if (Array.isArray(d.jobTypes)) setJobTypes(d.jobTypes.filter(isAllowedJobType));
          if (Array.isArray(d.skills)) setSkills(d.skills);
          if (Array.isArray(d.certs)) setCerts(d.certs);
          if (typeof d.availMorning === "boolean") setAvailMorning(d.availMorning);
          if (typeof d.availEvening === "boolean") setAvailEvening(d.availEvening);
          if (typeof d.availWeekend === "boolean") setAvailWeekend(d.availWeekend);
          if (d.bankAccount != null) setBankAccount(d.bankAccount);
          if (d.bankName != null) setBankName(d.bankName);
          if (typeof d.workPassNa === "boolean") setWorkPassNa(d.workPassNa);
          if (typeof d.mapLat === "number" && Number.isFinite(d.mapLat) && typeof d.mapLng === "number" && Number.isFinite(d.mapLng)) {
            setMapLat(d.mapLat);
            setMapLng(d.mapLng);
            mapFromDraft = true;
          } else if (d.lat != null && d.lng != null) {
            const la = typeof d.lat === "number" ? d.lat : parseFloat(String(d.lat));
            const ln = typeof d.lng === "number" ? d.lng : parseFloat(String(d.lng));
            if (Number.isFinite(la) && Number.isFinite(ln)) {
              setMapLat(la);
              setMapLng(ln);
              mapFromDraft = true;
            }
          }
        }
        const p = await api<Profile>("/workers/me/profile");
        setServerPct(p.profile_completeness_pct);
        setFullName((x) => x || p.full_name || "");
        setPhone((x) => x || p.phone || "");
        setEmail((x) => x || p.email || "");
        setLangs((x) => (x.length ? x : p.languages || []));
        setJobTypes((x) =>
          x.length ? x.filter(isAllowedJobType) : (p.job_type_preferences || []).filter(isAllowedJobType),
        );
        setSkills((x) => (x.length ? x : p.skills || []));
        setCerts((x) => (x.length ? x : p.certifications || []));
        const a = p.availability || {};
        if (a.morning) setAvailMorning(true);
        if (a.evening) setAvailEvening(true);
        if (a.weekend) setAvailWeekend(true);
        setBankName((x) => x || p.bank_name || "");
        setWorkPassNa(p.work_pass_not_applicable);
        if (!mapFromDraft && p.location_lat != null && p.location_lng != null) {
          setMapLat(p.location_lat);
          setMapLng(p.location_lng);
        }
        const docs = await api<{ doc_type: string }[]>("/workers/me/documents");
        setDocTypes(new Set(docs.map((d) => d.doc_type)));
        if (p.onboarding_completed) nav("/worker", { replace: true });
      } catch {
        /* ignore */
      }
    })();
  }, []);

  function removeLang(i: number) {
    setLangs((prev) => prev.filter((_, idx) => idx !== i));
  }

  function addLanguage(raw: string) {
    const canonical = canonicalizeLanguage(raw);
    if (!canonical) return;
    setLangs((prev) => (prev.includes(canonical) ? prev : [...prev, canonical]));
    setLangInput("");
  }

  function useCurrentLocation() {
    setMapGeoMsg(null);
    if (!navigator.geolocation) {
      setMapGeoMsg(t("map_geo_unavailable"));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setMapGeoMsg(null);
        setMapLat(pos.coords.latitude);
        setMapLng(pos.coords.longitude);
      },
      (err) => {
        if (err.code === err.PERMISSION_DENIED) setMapGeoMsg(t("map_geo_denied"));
        else setMapGeoMsg(t("map_geo_error"));
      },
      { enableHighAccuracy: true, timeout: 12_000, maximumAge: 60_000 },
    );
  }

  async function uploadFile(docType: string, file: File) {
    setMsg(null);
    setUploadedDocNames((m) => ({ ...m, [docType]: file.name }));
    const token = await getValidToken();
    const fd = new FormData();
    fd.append("file", file);
    setUploadPct((m) => ({ ...m, [docType]: 5 }));
    const tick = window.setInterval(() => {
      setUploadPct((m) => {
        const c = m[docType] ?? 0;
        return { ...m, [docType]: Math.min(92, c + 12) };
      });
    }, 120);
    try {
      const res = await fetch(apiUrl(`/workers/me/documents?doc_type=${encodeURIComponent(docType)}`), {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      });
      clearInterval(tick);
      if (!res.ok) throw new Error(await res.text());
      setUploadPct((m) => ({ ...m, [docType]: 100 }));
      setDocTypes((prev) => new Set([...prev, docType]));
      setUploadedDocNames((m) => ({ ...m, [docType]: file.name }));
      const p = await api<Profile>("/workers/me/profile");
      setServerPct(p.profile_completeness_pct);
    } catch (e) {
      clearInterval(tick);
      setMsg(String(e));
      setUploadPct((m) => ({ ...m, [docType]: 0 }));
      setUploadedDocNames((m) => ({ ...m, [docType]: null }));
    }
  }

  async function finish() {
    setMsg(null);
    try {
      if (!terms || !pdpa) {
        setMsg(t("onb_review_must_accept"));
        return;
      }
      await saveRemote();
      if (pdpa) await api("/workers/me/pdpa-consent", { method: "POST" });
      await api("/workers/me/onboarding/complete", {
        method: "POST",
        json: { terms_accepted: true },
      });
      localStorage.removeItem(DRAFT_KEY);
      setStep(6);
    } catch (e) {
      setMsg(String(e));
    }
  }

  const totalSteps = 5;
  const stepNav = [
    { index: 1, icon: "person_outline", label: t("onb_step_basic") },
    { index: 3, icon: "account_balance", label: t("onb_step_bank") },
    { index: 2, icon: "settings_suggest", label: t("onb_step_prefs") },
    { index: 4, icon: "folder", label: t("onb_step_docs") },
    { index: 5, icon: "task_alt", label: t("onb_step_review") },
  ];

  return (
    <div className="flex min-h-screen flex-col bg-surface text-on-surface">
      <header className="sticky top-0 z-40 h-16 border-b border-black/10 bg-white">
        <div className="mx-auto flex h-full max-w-[1440px] items-center justify-between px-4 md:px-8">
          <span className="truncate text-xl font-bold tracking-tight text-black">MCI Workforce</span>
          <div className="hidden items-center gap-4 md:flex" />
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <aside className="hidden w-72 shrink-0 flex-col gap-4 bg-[#f3f3f3] px-6 py-8 md:flex">
          <div className="mb-8">
            <h2 className="text-2xl font-headline font-extrabold tracking-tight text-on-surface">Onboarding</h2>
            <div className="mt-4">
              <div className="mb-2 flex items-end justify-between">
                <span className="text-[10px] font-bold uppercase tracking-widest text-on-surface-variant">{pct}% Complete</span>
              </div>
              <div className="h-1 w-full overflow-hidden rounded-full bg-surface-container-highest">
                <div className="h-full bg-primary transition-all duration-300" style={{ width: `${pct}%` }} />
              </div>
              <p className="mt-2 text-[11px] text-on-surface-variant">{progressHint(pct, t)}</p>
            </div>
          </div>
          <nav className="flex flex-col gap-1">
            {stepNav.map((item) => {
              const active = item.index === step;
              return (
                <button
                  key={item.index}
                  type="button"
                  onClick={() => setStep(item.index)}
                  className={[
                    "flex items-center gap-3 rounded-pill px-4 py-3 text-left transition-all",
                    active
                      ? "translate-x-1 bg-black font-bold text-white"
                      : "text-on-surface-variant hover:bg-white/40",
                  ].join(" ")}
                >
                  <span className="material-symbols-outlined" style={{ fontVariationSettings: active ? "'FILL' 1" : "'FILL' 0" }}>
                    {item.icon}
                  </span>
                  <span className="text-sm">{item.label}</span>
                </button>
              );
            })}
          </nav>
        </aside>

        <main className="flex-1 overflow-y-auto bg-surface-container-low px-4 py-8 md:px-16 md:py-12">
          <div className="mx-auto max-w-4xl">
            <header className="mb-10">
              <div className="mb-4 flex items-center gap-4">
                <span className="text-lg font-headline font-bold text-primary">{String(Math.min(step, totalSteps)).padStart(2, "0")}</span>
                <div className="h-px flex-1 bg-outline-variant/30" />
              </div>
              <h1 className="mb-2 text-4xl font-headline font-extrabold tracking-tight text-on-background md:text-5xl">
                {step === 1
                  ? t("onb_step_basic")
                  : step === 2
                    ? t("onb_step_prefs")
                    : step === 3
                      ? t("onb_step_bank")
                      : step === 4
                        ? t("onb_step_docs")
                        : t("onb_step_review")}
              </h1>
              <p className="max-w-xl text-on-surface-variant">
                {step === 1
                  ? "Define your professional identity. These details are used for compliance and verification."
                  : step === 2
                    ? "Set your preferences and location to personalize shift matching."
                    : step === 3
                      ? "Provide payout details securely for payroll processing."
                      : step === 4
                        ? "Upload required documents to complete compliance checks."
                        : "Review all details before final submission."}
              </p>
            </header>

            <section className="relative overflow-hidden rounded-featured bg-surface-container-lowest p-6 shadow-card md:p-10">
              <div className="pointer-events-none absolute -right-20 -top-20 h-56 w-56 rounded-full bg-black/5 blur-3xl" />
              <div className="relative z-10">
        {step === 1 && (
          <div className="grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
            <div className="group">
              <label className="text-xs text-on-surface-variant">{t("wizard_name")}</label>
              <input
                className="w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-3 py-2 text-on-surface"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Enter your full legal name"
                type="text"
              />
            </div>
            <div className="group">
              <label className="text-xs text-on-surface-variant">{t("onb_nric")}</label>
              <input
                className="w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-3 py-2 text-on-surface tracking-wider"
                value={nric}
                onChange={(e) => setNric(e.target.value)}
                placeholder="SXXXX123A"
                type="text"
              />
            </div>
            <div className="group">
              <label className="text-xs text-on-surface-variant">{t("phone")}</label>
              <input
                className="w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-3 py-2 text-on-surface"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                type="tel"
              />
            </div>
            <div className="group">
              <label className="text-xs text-on-surface-variant">Email Address</label>
              <input
                className="w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-3 py-2 text-on-surface"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                type="email"
              />
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-3">
            <label className="text-xs text-on-surface-variant">{t("wizard_languages")}</label>
            <div className="flex gap-2">
              <input
                className="flex-1 rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-3 py-2 text-on-surface"
                value={langInput}
                onChange={(e) => setLangInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addLanguage(langInput)}
                placeholder="Search language"
              />
              <button
                type="button"
                className="px-3 rounded-pill bg-black text-white text-sm font-semibold hover:brightness-95 active:scale-95"
                onClick={() => addLanguage(langInput)}
              >
                +
              </button>
            </div>

            {languageSuggestions.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {languageSuggestions.map((l) => (
                  <button
                    key={l}
                    type="button"
                    className="text-xs rounded-lg bg-primary/15 border border-primary/40 px-2 py-1 text-on-surface font-semibold hover:bg-primary/20"
                    onClick={() => addLanguage(l)}
                  >
                    + {l}
                  </button>
                ))}
              </div>
            )}

            <div className="flex flex-wrap gap-1">
              {langs.map((l, i) => (
                <button
                  key={`${l}-${i}`}
                  type="button"
                  className="text-xs rounded-lg bg-primary/15 border border-primary/40 px-2 py-1 text-on-surface font-semibold hover:bg-primary/20"
                  onClick={() => removeLang(i)}
                  title="Remove language"
                >
                  {l} x
                </button>
              ))}
            </div>
            <label className="text-xs text-on-surface-variant">{t("wizard_job_types")}</label>
            <JobTypeMultiPicker
              t={t}
              selected={jobTypes}
              onChange={setJobTypes}
              selectClassName="flex-1 min-w-0 rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-3 py-2 text-sm"
              addButtonClassName="px-3 rounded-xl bg-surface-container-high text-sm shrink-0"
              chipButtonClassName="text-xs bg-surface-container-high px-2 py-1 rounded-lg text-on-surface"
            />
            <p className="text-xs text-on-surface-variant">{t("onb_availability")}</p>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={availMorning} onChange={(e) => setAvailMorning(e.target.checked)} />
              {t("onb_av_morning")}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={availEvening} onChange={(e) => setAvailEvening(e.target.checked)} />
              {t("onb_av_evening")}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={availWeekend} onChange={(e) => setAvailWeekend(e.target.checked)} />
              {t("onb_av_weekend")}
            </label>
            <label className="text-xs text-on-surface-variant">{t("wizard_skills")}</label>
            <SkillMultiPicker
              t={t}
              selected={skills}
              onChange={setSkills}
              inputClassName="flex-1 rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-3 py-2 text-on-surface"
              addButtonClassName="px-3 rounded-xl bg-surface-container-high text-sm shrink-0"
              chipButtonClassName="text-xs bg-surface-container-high px-2 py-1 rounded-lg text-on-surface"
            />
            <label className="text-xs text-on-surface-variant">{t("onb_certs_optional")}</label>
            <CertificationMultiPicker
              t={t}
              selected={certs}
              onChange={setCerts}
              inputClassName="flex-1 rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-3 py-2 text-on-surface"
              addButtonClassName="px-3 rounded-xl bg-surface-container-high text-sm shrink-0"
              chipButtonClassName="text-xs bg-surface-container-high px-2 py-1 rounded-lg text-on-surface"
            />
            <label className="text-xs text-on-surface-variant">{t("wizard_location")}</label>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                className="rounded-pill bg-black px-3 py-2 text-sm font-semibold text-white hover:brightness-95 active:scale-95"
                onClick={useCurrentLocation}
              >
                {t("map_use_my_location")}
              </button>
              {mapGeoMsg && <p className="text-xs text-error">{mapGeoMsg}</p>}
            </div>
            <LocationMapPicker
              latitude={mapLat}
              longitude={mapLng}
              onChange={({ lat, lng }) => {
                setMapGeoMsg(null);
                setMapLat(lat);
                setMapLng(lng);
              }}
              lightMode
              height="220px"
              showGeolocationButton={false}
            />
          </div>
        )}

        {step === 3 && (
          <div className="space-y-3">
            <label className="text-xs text-on-surface-variant">{t("onb_bank_account")}</label>
            <input
              type="password"
              className="w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-3 py-2 text-on-surface"
              value={bankAccount}
              onChange={(e) => setBankAccount(e.target.value)}
              autoComplete="off"
            />
            <label className="text-xs text-on-surface-variant">{t("onb_bank_name_optional")}</label>
            <input
              className="w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-3 py-2 text-on-surface"
              value={bankName}
              onChange={(e) => setBankName(e.target.value)}
            />
          </div>
        )}

        {step === 4 && (
          <div className="space-y-4">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={workPassNa} onChange={(e) => setWorkPassNa(e.target.checked)} />
              {t("onb_work_pass_na")}
            </label>
            {DOC_SLOTS.map((slot) => (
              <div key={slot} className="border border-outline-variant/20 rounded-xl p-3 space-y-2 bg-surface-container-lowest">
                <div className="flex justify-between items-center">
                  <span className="text-sm font-medium text-on-surface">{t(`onb_doc_${slot}`)}</span>
                  {docTypes.has(slot) && <span className="text-xs text-emerald-700">{t("onb_uploaded")}</span>}
                </div>
                {uploadedDocNames[slot] ? (
                  <p className="text-[11px] text-on-surface-variant line-clamp-1">{uploadedDocNames[slot]}</p>
                ) : null}
                <input
                  type="file"
                  accept="image/*,.pdf,application/pdf"
                  className="w-full cursor-pointer text-xs file:cursor-pointer file:rounded-xl file:border file:border-outline-variant/30 file:bg-surface-container-high file:px-4 file:py-2 file:text-sm file:font-semibold file:text-on-surface hover:file:bg-surface-container-highest"
                  disabled={slot === "work_pass" && workPassNa}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) uploadFile(slot, f);
                  }}
                />
                {(uploadPct[slot] ?? 0) > 0 && (uploadPct[slot] ?? 0) < 100 && (
                  <div className="h-1 bg-surface-container-high rounded overflow-hidden">
                    <div className="h-full bg-primary transition-all" style={{ width: `${uploadPct[slot]}%` }} />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {step === 5 && (
          <div className="space-y-4">
            <div className="text-sm space-y-2 text-on-surface-variant">
              <p>
                <span className="text-on-surface-variant">{t("wizard_name")}:</span> {fullName || "—"}
              </p>
              <p>
                <span className="text-on-surface-variant">{t("onb_nric")}:</span> {nric ? "••••" + nric.slice(-4) : "—"}
              </p>
              <p>
                <span className="text-on-surface-variant">{t("phone")}:</span> {phone || "—"}
              </p>
              <p>
                <span className="text-on-surface-variant">Email:</span> {email || "—"}
              </p>
              <p>
                <span className="text-on-surface-variant">{t("wizard_languages")}:</span> {langs.join(", ") || "—"}
              </p>
              <p>
                <span className="text-on-surface-variant">{t("wizard_job_types")}:</span> {jobTypes.join(", ") || "—"}
              </p>
              <p>
                <span className="text-on-surface-variant">{t("onb_bank_account")}:</span>{" "}
                {bankAccount ? maskBank(bankAccount) : "—"}
              </p>
              <p>
                <span className="text-on-surface-variant">{t("onb_documents")}:</span> {[...docTypes].join(", ") || "—"}
              </p>
            </div>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} className="mt-1" />
              {t("onb_terms")}
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={pdpa} onChange={(e) => setPdpa(e.target.checked)} className="mt-1" />
              {t("wizard_pdpa")}
            </label>
          </div>
        )}

        {step === 6 && (
          <div className="text-center py-8 space-y-4">
            <div className="text-4xl text-primary">✓</div>
            <h2 className="text-xl font-bold">{t("onb_done_title")}</h2>
            <p className="text-sm text-on-surface-variant">{t("onb_done_body")}</p>
            <button type="button" className="bg-primary text-on-primary font-semibold px-10 py-4 rounded-xl" onClick={() => nav("/worker")}>
              {t("onb_done_cta")}
            </button>
          </div>
        )}

        {msg && <p className="text-sm text-error mt-3">{msg}</p>}
              </div>
            </section>
          </div>
        </main>
      </div>

      {step >= 1 && step <= 5 && (
        <div className="sticky bottom-0 z-30 border-t border-outline-variant/20 bg-surface-container-lowest/95 p-4">
          <div className="mx-auto flex max-w-lg gap-2">
          {step > 1 && (
            <button
              type="button"
              className="flex-1 bg-surface-container-high py-3 rounded-xl text-sm font-semibold text-on-surface"
              onClick={() => setStep((s) => s - 1)}
            >
              {t("wizard_back")}
            </button>
          )}
          {step < 5 && (
            <button
              type="button"
              className="flex-1 bg-primary text-on-primary font-semibold py-3 rounded-xl text-sm"
              onClick={() => setStep((s) => s + 1)}
            >
              {t("wizard_next")}
            </button>
          )}
          {step === 5 && (
            <button type="button" className="flex-1 bg-primary text-on-primary font-semibold py-3 rounded-xl text-sm" onClick={finish}>
              {t("onb_submit")}
            </button>
          )}
          </div>
        </div>
      )}
    </div>
  );
}
