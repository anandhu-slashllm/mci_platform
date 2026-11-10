import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, useNavigate } from "react-router-dom";
import { api, apiUrl, getValidToken } from "../../api";
import { ClientPortalLayout } from "../../components/client/ClientPortalLayout";
import { fmtShiftDate, formatSgd, formatTimesheetRate } from "../../utils/timesheetFormat";
import { JobOrderForm } from "../../components/jobOrder/JobOrderForm";
import { jobStatusLabelKey } from "../../components/jobOrder/jobOrderStatus";

type JobRow = {
  id: number;
  title: string;
  status: string;
  job_type: string;
  location_address: string;
  filled_slots: number;
  total_slots: number;
};

function jobCardStatusStyle(status: string): string {
  switch (status) {
    case "draft":
      return "bg-surface-container-high text-on-surface";
    case "open":
      return "bg-primary-container text-on-primary-container";
    case "partially_filled":
      return "bg-tertiary-container text-on-tertiary-container";
    case "fully_filled":
      return "bg-secondary-container text-on-secondary-container";
    case "completed":
      return "bg-surface-container-highest text-on-surface";
    case "cancelled":
      return "bg-error-container/30 text-on-error-container";
    default:
      return "bg-surface-container-high text-on-surface";
  }
}

function truncate(s: string, n: number) {
  if (s.length <= n) return s;
  return `${s.slice(0, n - 1)}…`;
}

function ActiveJobCard({ job, onOpen, t }: { job: JobRow; onOpen: (jobId: number) => void; t: (key: string) => string }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(job.id)}
      className="text-left bg-surface-container-lowest p-6 rounded-3xl group hover:bg-white dark:hover:bg-slate-800 transition-all duration-300 border border-transparent hover:shadow-[0_10px_40px_rgba(43,52,55,0.08)] flex flex-col justify-between min-h-[18rem] relative overflow-hidden"
    >
      <div className="absolute top-0 right-0 w-32 h-32 bg-primary/5 rounded-full -mr-16 -mt-16 group-hover:scale-110 transition-transform duration-500 pointer-events-none" />
      <div>
        <div className="flex justify-between items-start mb-6">
          <span className={`px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest ${jobCardStatusStyle(job.status)}`}>
            {t(jobStatusLabelKey(job.status))}
          </span>
          <span className="text-on-surface-variant opacity-40 group-hover:opacity-70">
            <span className="material-symbols-outlined">arrow_forward</span>
          </span>
        </div>
        <h3 className="font-headline text-xl md:text-2xl font-extrabold text-on-surface leading-tight mb-2 group-hover:text-primary transition-colors">
          {job.title}
        </h3>
        <div className="flex flex-col gap-2 text-on-surface-variant">
          <div className="flex items-center gap-1.5">
            <span className="material-symbols-outlined text-sm">location_on</span>
            <span className="text-xs font-medium">{truncate(job.location_address, 42)}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="material-symbols-outlined text-sm">calendar_today</span>
            <span className="text-xs font-medium">{t("job_card_schedule_detail")}</span>
          </div>
        </div>
      </div>
      <div className="flex items-end justify-between mt-8">
        <div>
          <p className="text-[10px] font-bold text-on-surface-variant uppercase tracking-widest mb-1">{job.job_type}</p>
          <div className="flex items-center gap-2">
            <span className="text-2xl font-bold text-primary">{job.filled_slots}</span>
            <span className="text-lg text-outline-variant font-medium">/ {job.total_slots} {t("job_detail_fill_short")}</span>
          </div>
        </div>
        <div className="w-12 h-12 rounded-full bg-surface-container flex items-center justify-center text-primary group-hover:bg-primary group-hover:text-white transition-all duration-300">
          <span className="material-symbols-outlined">arrow_forward</span>
        </div>
      </div>
    </button>
  );
}

function JobHistoryList({ rows, onOpen, t }: { rows: JobRow[]; onOpen: (jobId: number) => void; t: (key: string) => string }) {
  if (rows.length === 0) {
    return <p className="text-sm text-on-surface-variant">{t("client_job_history_empty")}</p>;
  }
  return (
    <div className="space-y-3">
      {rows.map((j) => (
        <button
          key={j.id}
          type="button"
          onClick={() => onOpen(j.id)}
          className="w-full rounded-2xl border border-outline-variant/20 bg-surface p-4 text-left transition-colors hover:bg-surface-container"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-semibold text-on-surface truncate">{j.title}</p>
              <p className="mt-1 text-xs text-on-surface-variant">{j.job_type}</p>
            </div>
            <span className={`shrink-0 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest ${jobCardStatusStyle(j.status)}`}>
              {t(jobStatusLabelKey(j.status))}
            </span>
          </div>
          <div className="mt-3 flex items-center justify-between gap-3 text-xs text-on-surface-variant">
            <span className="truncate">{truncate(j.location_address, 28)}</span>
            <span>
              {j.filled_slots}/{j.total_slots} {t("job_detail_fill_short")}
            </span>
          </div>
        </button>
      ))}
    </div>
  );
}

export function ClientLayout() {
  return <ClientPortalLayout />;
}

export function ClientJobsList() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [rows, setRows] = useState<JobRow[]>([]);
  useEffect(() => {
    api<JobRow[]>("/job-orders").then(setRows);
  }, []);
  const activeRows = useMemo(
    () => rows.filter((j) => j.status !== "completed" && j.status !== "cancelled"),
    [rows],
  );

  return (
    <>
      <header className="flex flex-col md:flex-row md:items-end justify-between gap-6 mb-10 md:mb-12">
        <div>
          <h1 className="font-headline text-4xl md:text-5xl font-extrabold text-on-surface tracking-tighter mb-2">
            {t("job_orders")}
          </h1>
          <p className="text-on-surface-variant font-medium tracking-wide text-sm md:text-base">
            {t("client_job_list_subtitle")}
          </p>
        </div>
        <div className="flex gap-3">
          <button
            type="button"
            className="bg-surface-container-highest text-on-surface px-5 md:px-6 py-3 rounded-xl font-semibold text-sm hover:bg-surface-variant transition-colors flex items-center gap-2 active:scale-95 border border-outline-variant/20"
          >
            <span className="material-symbols-outlined text-[20px]">filter_list</span>
            {t("client_filter")}
          </button>
          <button
            type="button"
            onClick={() => nav("/client/new")}
            className="glass-cta text-white px-5 md:px-6 py-3 rounded-xl font-bold text-sm shadow-lg flex items-center gap-2 active:scale-95"
          >
            <span className="material-symbols-outlined text-[20px]">add</span>
            {t("job_form_new")}
          </button>
        </div>
      </header>

      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-on-surface">{t("client_active_jobs_heading")}</h2>
            <p className="text-sm text-on-surface-variant">{t("client_active_jobs_subtitle")}</p>
          </div>
          <span className="rounded-full bg-surface-container px-3 py-1 text-xs font-semibold text-on-surface">{activeRows.length}</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
          {activeRows.map((j) => (
            <ActiveJobCard key={j.id} job={j} onOpen={(jobId) => nav(`/client/${jobId}`)} t={t} />
          ))}

          {activeRows.length === 0 && (
            <div className="rounded-3xl border border-outline-variant/25 bg-surface-container-lowest p-6 text-sm text-on-surface-variant md:col-span-2 xl:col-span-2">
              {t("client_active_jobs_empty")}
            </div>
          )}

          <NavLink
            to="/client/new"
            className="border-2 border-dashed border-outline-variant/40 p-6 rounded-3xl group hover:border-primary/50 transition-all duration-300 flex flex-col items-center justify-center min-h-[18rem] text-center"
          >
            <div className="w-16 h-16 rounded-full bg-surface-container flex items-center justify-center text-outline-variant group-hover:bg-primary group-hover:text-white transition-all duration-500 mb-4">
              <span className="material-symbols-outlined text-3xl">add_circle</span>
            </div>
            <h3 className="font-headline text-xl font-bold text-on-surface-variant group-hover:text-on-surface transition-colors">
              {t("job_form_new")}
            </h3>
            <p className="text-outline text-sm mt-1">{t("client_create_job_hint")}</p>
          </NavLink>
        </div>
      </div>
    </>
  );
}

export function ClientJobHistoryPage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [rows, setRows] = useState<JobRow[]>([]);
  useEffect(() => {
    api<JobRow[]>("/job-orders").then(setRows);
  }, []);
  const historyRows = useMemo(
    () => rows.filter((j) => j.status === "completed" || j.status === "cancelled").sort((a, b) => b.id - a.id),
    [rows],
  );

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-headline text-3xl md:text-4xl font-extrabold text-on-surface tracking-tight mb-2">
            {t("client_job_history_heading")}
          </h1>
          <p className="text-on-surface-variant text-sm md:text-base">{t("client_job_history_subtitle")}</p>
        </div>
        <span className="rounded-full bg-surface-container px-3 py-1 text-xs font-semibold text-on-surface">{historyRows.length}</span>
      </header>

      <div className="rounded-3xl border border-outline-variant/20 bg-surface-container-lowest p-5">
        <JobHistoryList rows={historyRows} onOpen={(jobId) => nav(`/client/${jobId}`)} t={t} />
      </div>
    </div>
  );
}

export function ClientNewJob() {
  const { t } = useTranslation();
  const nav = useNavigate();
  return (
    <div>
      <h1 className="font-headline text-3xl md:text-4xl font-extrabold text-on-surface tracking-tight mb-2">{t("job_form_new")}</h1>
      <p className="text-on-surface-variant text-sm mb-8">{t("client_new_job_intro")}</p>
      <JobOrderForm t={t} mode="client" lightMode onSuccess={() => nav("/client")} />
    </div>
  );
}

type ClientTimesheetRow = {
  id: number;
  job_order_id: number;
  job_title?: string;
  shift_id: number;
  status: string;
  gross_pay: number;
  scheduled_hours?: number;
  actual_hours?: number;
  overtime_hours?: number;
  hourly_rate_snapshot?: number;
  per_shift_rate_snapshot?: number | null;
  pay_model?: string;
  worker_name?: string;
  shift_starts_at?: string | null;
  shift_ends_at?: string | null;
  shift_date?: string | null;
  clock_in_at?: string | null;
  clock_out_at?: string | null;
  dispute_flag?: boolean;
  dispute_note?: string | null;
};

function fmtTsIso(iso: string | undefined | null): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function formatTimesheetStatus(status: string): string {
  return status.replace(/_/g, " ");
}

function timesheetStatusBadgeClass(status: string): string {
  switch (status) {
    case "disputed":
      return "bg-error-container/30 text-on-error-container";
    case "payment_released":
    case "client_payroll_released":
      return "bg-secondary-container text-on-secondary-container";
    case "ta_verified":
    case "employer_endorsed":
      return "bg-primary-container text-on-primary-container";
    default:
      return "bg-surface-container-high text-on-surface";
  }
}

function canDisputeTimesheet(status: string): boolean {
  return (
    status === "pending" ||
    status === "employer_endorsed" ||
    status === "ta_verified" ||
    status === "client_payroll_released" ||
    status === "disputed"
  );
}

type ClientContractContext = {
  has_ta_payroll: boolean;
  active_ta_user_id: number | null;
  active_ta_display_name?: string | null;
  active_ta_username?: string | null;
  active_contract_id: number | null;
  pending_contracts: {
    id: number;
    ta_user_id: number;
    status: string;
    ta_display_name?: string | null;
    ta_username?: string | null;
  }[];
};

/** Human-readable TA label for clients (display name, then username, then id). */
function formatClientTaName(
  displayName: string | null | undefined,
  username: string | null | undefined,
  userId: number | null | undefined,
): string {
  const d = displayName?.trim();
  if (d) return d;
  const u = username?.trim();
  if (u) return u;
  if (userId != null) return `TA #${userId}`;
  return "—";
}

export function ClientTimesheetsPage() {
  const { t } = useTranslation();
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [rows, setRows] = useState<ClientTimesheetRow[]>([]);
  const [contractCtx, setContractCtx] = useState<ClientContractContext | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [endorseTarget, setEndorseTarget] = useState<ClientTimesheetRow | null>(null);
  const [endorseStars, setEndorseStars] = useState(5);
  const [endorsePrefer, setEndorsePrefer] = useState(true);
  const [endorseErr, setEndorseErr] = useState<string | null>(null);
  const [disputeTarget, setDisputeTarget] = useState<ClientTimesheetRow | null>(null);
  const [disputeNote, setDisputeNote] = useState("");
  const [disputeErr, setDisputeErr] = useState<string | null>(null);
  const [payrollErr, setPayrollErr] = useState<string | null>(null);

  async function reload() {
    setLoadErr(null);
    try {
      const [j, ts, ctx] = await Promise.all([
        api<JobRow[]>("/job-orders"),
        api<ClientTimesheetRow[]>("/timesheets"),
        api<ClientContractContext>("/ta-contracts/client/context"),
      ]);
      setJobs(j);
      setRows(ts);
      setContractCtx(ctx);
    } catch (e) {
      setLoadErr(String(e));
    }
  }

  async function releaseClientPayroll(entryId: number) {
    setPayrollErr(null);
    try {
      await api(`/timesheets/${entryId}/client-payroll-release`, { method: "POST" });
      await reload();
    } catch (e) {
      setPayrollErr(String(e));
    }
  }

  async function downloadClientPayrollCsv() {
    setPayrollErr(null);
    const token = await getValidToken();
    const res = await fetch(apiUrl("/exports/payroll/client-lines.csv"), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      setPayrollErr(await res.text());
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "client_payroll_lines.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  useEffect(() => {
    void reload();
  }, []);

  function openEndorse(x: ClientTimesheetRow) {
    setEndorseErr(null);
    setEndorseStars(5);
    setEndorsePrefer(true);
    setEndorseTarget(x);
  }

  function openDispute(x: ClientTimesheetRow) {
    setDisputeErr(null);
    setDisputeNote(x.dispute_note?.trim() || "");
    setDisputeTarget(x);
  }

  async function submitEndorse() {
    if (!endorseTarget) return;
    setEndorseErr(null);
    try {
      await api(`/timesheets/${endorseTarget.id}/employer-endorse`, {
        method: "POST",
        json: { stars: endorseStars, prefer_worker: endorsePrefer },
      });
      setEndorseTarget(null);
      await reload();
    } catch (e) {
      setEndorseErr(String(e));
    }
  }

  async function submitDispute() {
    if (!disputeTarget) return;
    const note = disputeNote.trim();
    if (!note) {
      setDisputeErr(t("timesheet_dispute_comment_required"));
      return;
    }
    setDisputeErr(null);
    try {
      await api(`/timesheets/${disputeTarget.id}/dispute`, {
        method: "POST",
        json: { note },
      });
      setDisputeTarget(null);
      setDisputeNote("");
      await reload();
    } catch (e) {
      setDisputeErr(String(e));
    }
  }

  const entriesByJobId = useMemo(() => {
    const m = new Map<number, ClientTimesheetRow[]>();
    for (const r of rows) {
      const list = m.get(r.job_order_id) ?? [];
      list.push(r);
      m.set(r.job_order_id, list);
    }
    return m;
  }, [rows]);

  return (
    <div>
      {endorseTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="presentation"
          onClick={() => setEndorseTarget(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="endorse-dialog-title"
            className="bg-surface-container-lowest border border-outline-variant/25 rounded-2xl shadow-xl max-w-md w-full p-6 text-on-surface"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.key === "Escape" && setEndorseTarget(null)}
          >
            <h2 id="endorse-dialog-title" className="font-headline text-lg font-bold mb-4">
              {t("client_timesheet_endorse_modal_title")}
            </h2>
            <p className="text-sm text-on-surface-variant mb-2">{endorseTarget.worker_name || "—"}</p>
            <div className="mb-4">
              <p className="text-sm font-medium mb-2">{t("client_timesheet_endorse_rating")}</p>
              <div className="flex gap-2 flex-wrap">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button
                    key={n}
                    type="button"
                    className={`h-10 w-10 rounded-lg font-semibold text-sm border transition-colors ${
                      endorseStars === n
                        ? "bg-primary text-on-primary border-primary"
                        : "border-outline-variant/40 hover:bg-surface-container-high"
                    }`}
                    onClick={() => setEndorseStars(n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>
            <div className="mb-4">
              <p className="text-sm font-medium mb-1">{t("client_timesheet_endorse_prefer")}</p>
              <p className="text-xs text-on-surface-variant mb-2">{t("client_timesheet_endorse_prefer_hint")}</p>
              <div className="flex gap-3">
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="radio"
                    name="prefer"
                    checked={endorsePrefer}
                    onChange={() => setEndorsePrefer(true)}
                  />
                  {t("client_timesheet_endorse_prefer_yes")}
                </label>
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="radio"
                    name="prefer"
                    checked={!endorsePrefer}
                    onChange={() => setEndorsePrefer(false)}
                  />
                  {t("client_timesheet_endorse_prefer_no")}
                </label>
              </div>
            </div>
            {endorseErr && <p className="text-sm text-error mb-3">{endorseErr}</p>}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="px-4 py-2 rounded-xl text-sm font-medium border border-outline-variant/30"
                onClick={() => setEndorseTarget(null)}
              >
                {t("client_timesheet_endorse_cancel")}
              </button>
              <button
                type="button"
                className="px-4 py-2 rounded-xl text-sm font-semibold bg-primary text-on-primary"
                onClick={() => void submitEndorse()}
              >
                {t("client_timesheet_endorse_submit")}
              </button>
            </div>
          </div>
        </div>
      )}
      {disputeTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="presentation"
          onClick={() => setDisputeTarget(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="dispute-dialog-title"
            className="bg-surface-container-lowest border border-outline-variant/25 rounded-2xl shadow-xl max-w-md w-full p-6 text-on-surface"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.key === "Escape" && setDisputeTarget(null)}
          >
            <h2 id="dispute-dialog-title" className="font-headline text-lg font-bold mb-2">
              {t("timesheet_dispute_modal_title")}
            </h2>
            <p className="text-sm text-on-surface-variant mb-4">{disputeTarget.worker_name || "—"}</p>
            <label className="block text-sm font-medium mb-2" htmlFor="client-timesheet-dispute-note">
              {t("timesheet_dispute_comment_label")}
            </label>
            <textarea
              id="client-timesheet-dispute-note"
              className="min-h-28 w-full rounded-xl border border-outline-variant/30 bg-surface-container-low px-3 py-2 text-sm"
              value={disputeNote}
              onChange={(e) => setDisputeNote(e.target.value)}
              placeholder={t("timesheet_dispute_comment_placeholder")}
            />
            {disputeErr && <p className="mt-3 text-sm text-error">{disputeErr}</p>}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                className="px-4 py-2 rounded-xl text-sm font-medium border border-outline-variant/30"
                onClick={() => setDisputeTarget(null)}
              >
                {t("client_timesheet_endorse_cancel")}
              </button>
              <button
                type="button"
                className="px-4 py-2 rounded-xl text-sm font-semibold bg-error text-on-error"
                onClick={() => void submitDispute()}
              >
                {t("timesheet_dispute_submit")}
              </button>
            </div>
          </div>
        </div>
      )}
      <h1 className="font-headline text-3xl md:text-4xl font-extrabold text-on-surface tracking-tight mb-2">{t("timesheets")}</h1>
      <p className="text-on-surface-variant text-sm mb-8">{t("client_timesheets_intro")}</p>
      {contractCtx?.has_ta_payroll && (
        <p className="text-sm text-on-surface-variant mb-4 rounded-xl border border-outline-variant/25 bg-surface-container-low px-4 py-3">
          {t("client_timesheets_ta_payroll_notice")}
        </p>
      )}
      {!contractCtx?.has_ta_payroll && (
        <div className="flex flex-wrap items-center gap-3 mb-6">
          <button
            type="button"
            className="px-4 py-2 rounded-xl text-sm font-semibold bg-primary text-on-primary"
            onClick={() => void downloadClientPayrollCsv()}
          >
            {t("client_timesheets_download_payroll_csv")}
          </button>
          <span className="text-xs text-on-surface-variant max-w-xl">{t("client_timesheets_self_payroll_hint")}</span>
        </div>
      )}
      {payrollErr && <p className="mb-4 text-sm text-error">{payrollErr}</p>}
      {loadErr && <p className="mb-4 text-sm text-error">{loadErr}</p>}
      {jobs.length === 0 && !loadErr && <p className="text-on-surface-variant text-sm">{t("client_live_no_jobs")}</p>}
      <ul className="space-y-6">
        {jobs.map((job) => {
          const entries = entriesByJobId.get(job.id) ?? [];
          const title = entries[0]?.job_title || job.title;
          const totalGross = entries.reduce((s, x) => s + x.gross_pay, 0);
          const byShift = new Map<number, ClientTimesheetRow[]>();
          for (const r of entries) {
            const list = byShift.get(r.shift_id) ?? [];
            list.push(r);
            byShift.set(r.shift_id, list);
          }
          const shiftGroups = Array.from(byShift.values()).sort(
            (a, b) =>
              new Date(a[0]?.shift_starts_at || 0).getTime() - new Date(b[0]?.shift_starts_at || 0).getTime(),
          );
          return (
            <li
              key={job.id}
              className="bg-surface-container-lowest border border-outline-variant/25 rounded-2xl p-4 md:p-5 text-sm text-on-surface shadow-sm"
            >
              <div className="mb-3 flex flex-wrap items-start justify-between gap-2 border-b border-outline-variant/10 pb-3">
                <div>
                  <p className="font-headline text-lg font-bold text-on-surface">{title}</p>
                  <p className="text-xs text-on-surface-variant mt-1">
                    {entries.length === 0
                      ? t("client_timesheets_job_empty", { id: job.id })
                      : t("client_timesheets_job_meta", {
                          id: job.id,
                          count: entries.length,
                          total: totalGross.toFixed(2),
                        })}
                  </p>
                </div>
                <span
                  className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${jobCardStatusStyle(job.status)}`}
                >
                  {t(jobStatusLabelKey(job.status))}
                </span>
              </div>
              {entries.length === 0 ? (
                <p className="text-on-surface-variant text-sm">{t("client_timesheets_no_entries_for_job")}</p>
              ) : (
                <div className="space-y-6 pt-1">
                  {shiftGroups.map((shiftRows) => (
                    <div key={shiftRows[0]?.shift_id ?? 0}>
                      <h3 className="text-xs font-bold uppercase tracking-wide text-on-surface-variant mb-2">
                        {t("client_timesheet_shift_window")}: {fmtTsIso(shiftRows[0]?.shift_starts_at)} →{" "}
                        {fmtTsIso(shiftRows[0]?.shift_ends_at)}
                      </h3>
                      <div className="overflow-x-auto rounded-xl border border-outline-variant/20">
                        <table className="w-full min-w-[1040px] text-left text-sm">
                          <thead className="bg-surface-container-low text-xs uppercase text-on-surface-variant">
                            <tr>
                              <th className="px-3 py-2 font-semibold">{t("timesheet_col_worker")}</th>
                              <th className="px-3 py-2 font-semibold">{t("timesheet_col_shift_date")}</th>
                              <th className="px-3 py-2 font-semibold">{t("timesheet_col_scheduled")}</th>
                              <th className="px-3 py-2 font-semibold">{t("timesheet_col_actual")}</th>
                              <th className="px-3 py-2 font-semibold">{t("timesheet_col_overtime")}</th>
                              <th className="px-3 py-2 font-semibold">{t("timesheet_col_rate")}</th>
                              <th className="px-3 py-2 font-semibold">{t("timesheet_col_gross")}</th>
                              <th className="px-3 py-2 font-semibold">{t("timesheet_col_clock_in")}</th>
                              <th className="px-3 py-2 font-semibold">{t("timesheet_col_clock_out")}</th>
                              <th className="px-3 py-2 font-semibold">{t("timesheet_col_status")}</th>
                              <th className="px-3 py-2 font-semibold" />
                            </tr>
                          </thead>
                          <tbody>
                            {shiftRows.map((x) => (
                              <tr key={x.id} className="border-t border-outline-variant/10">
                                <td className="px-3 py-2 font-medium">{x.worker_name || "—"}</td>
                                <td className="px-3 py-2 text-on-surface-variant">
                                  {fmtShiftDate(x.shift_date || (x.shift_starts_at ? x.shift_starts_at.slice(0, 10) : null))}
                                </td>
                                <td className="px-3 py-2">
                                  {x.scheduled_hours != null ? x.scheduled_hours.toFixed(2) : "—"}
                                </td>
                                <td className="px-3 py-2">{x.actual_hours != null ? x.actual_hours.toFixed(2) : "—"}</td>
                                <td className="px-3 py-2">
                                  {x.overtime_hours != null && x.overtime_hours > 0 ? x.overtime_hours.toFixed(2) : "—"}
                                </td>
                                <td className="px-3 py-2 text-on-surface-variant">
                                  {x.pay_model
                                    ? formatTimesheetRate({
                                        pay_model: x.pay_model,
                                        hourly_rate_snapshot: x.hourly_rate_snapshot ?? 0,
                                        per_shift_rate_snapshot: x.per_shift_rate_snapshot ?? null,
                                      })
                                    : "—"}
                                </td>
                                <td className="px-3 py-2 font-semibold">{formatSgd(x.gross_pay)}</td>
                                <td className="px-3 py-2 text-on-surface-variant">{fmtTsIso(x.clock_in_at)}</td>
                                <td className="px-3 py-2 text-on-surface-variant">{fmtTsIso(x.clock_out_at)}</td>
                                <td className="px-3 py-2">
                                  <span
                                    className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${timesheetStatusBadgeClass(x.status)}`}
                                  >
                                    {formatTimesheetStatus(x.status)}
                                  </span>
                                  {(x.dispute_flag || (x.dispute_note && x.dispute_note.trim())) && (
                                    <p className="mt-1 max-w-xs text-xs text-error">{x.dispute_note?.trim() || "—"}</p>
                                  )}
                                </td>
                                <td className="px-3 py-2">
                                  <div className="flex flex-col gap-1 items-start">
                                    {x.status === "pending" && (
                                      <button
                                        type="button"
                                        className="text-primary font-semibold hover:underline"
                                        onClick={() => openEndorse(x)}
                                      >
                                        {t("client_timesheet_endorse")}
                                      </button>
                                    )}
                                    {!contractCtx?.has_ta_payroll && x.status === "employer_endorsed" && (
                                      <button
                                        type="button"
                                        className="text-primary font-semibold hover:underline"
                                        onClick={() => void releaseClientPayroll(x.id)}
                                      >
                                        {t("client_timesheet_release_payroll")}
                                      </button>
                                    )}
                                    {canDisputeTimesheet(x.status) && (
                                      <button
                                        type="button"
                                        className="text-error font-semibold hover:underline"
                                        onClick={() => openDispute(x)}
                                      >
                                        {t("timesheet_flag_for_review")}
                                      </button>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

type LiveWorkerRow = {
  worker_name: string;
  assignment_id: number;
  shift_id?: number;
  scheduled_start?: string;
  clock_in: string | null;
  clock_out: string | null;
  geofence_ok: boolean | null;
  photo_in: string | null;
  attendance_status?: string | null;
  show_late_badge?: boolean;
  not_checked_in_late?: boolean;
  show_absent_badge?: boolean;
};

type ShiftAttendanceBlock = {
  shift_id: number;
  starts_at: string;
  ends_at: string;
  workers: LiveWorkerRow[];
};

type ShiftAttendanceData = {
  job_id: number;
  shifts: ShiftAttendanceBlock[];
};

function fmtLiveIso(iso: string | undefined | null): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
  } catch {
    return iso;
  }
}

type LiveBanner = {
  id: string;
  kind: "in" | "out";
  worker_name: string;
  timeLabel: string;
};

function LiveWorkerStatusCells({ w }: { w: LiveWorkerRow }) {
  const { t } = useTranslation();
  const absent = Boolean(w.show_absent_badge || w.attendance_status === "absent");
  const notCheckedIn = Boolean(w.not_checked_in_late);
  const arrivedLate = Boolean(w.clock_in && w.show_late_badge && !notCheckedIn && !absent);
  const geofenceBad = w.geofence_ok === false;
  return (
    <div className="flex flex-wrap gap-1.5">
      {geofenceBad && (
        <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-900 dark:bg-amber-900/40 dark:text-amber-200">
          {t("live_badge_geofence")}
        </span>
      )}
      {absent && (
        <span className="rounded-full bg-rose-100 px-2.5 py-0.5 text-xs font-semibold text-rose-900 dark:bg-rose-900/40 dark:text-rose-200">
          {t("live_badge_absent")}
        </span>
      )}
      {notCheckedIn && (
        <span className="rounded-full bg-rose-100 px-2.5 py-0.5 text-xs font-semibold text-rose-900 dark:bg-rose-900/40 dark:text-rose-200">
          {t("live_badge_not_checked_in")}
        </span>
      )}
      {arrivedLate && (
        <span className="rounded-full bg-orange-100 px-2.5 py-0.5 text-xs font-semibold text-orange-900 dark:bg-orange-900/40 dark:text-orange-200">
          {t("live_badge_late")}
        </span>
      )}
      {!geofenceBad && !absent && !notCheckedIn && !arrivedLate && w.clock_in && (
        <span className="text-xs text-on-surface-variant">OK</span>
      )}
    </div>
  );
}

export function ClientLivePage() {
  const { t } = useTranslation();
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [jobsErr, setJobsErr] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [shiftData, setShiftData] = useState<ShiftAttendanceData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [banners, setBanners] = useState<LiveBanner[]>([]);
  const prevPunchesRef = useRef<Map<number, { clock_in: string | null; clock_out: string | null }> | null>(null);

  useEffect(() => {
    setJobsErr(null);
    api<JobRow[]>("/job-orders")
      .then(setJobs)
      .catch((e) => setJobsErr(String(e)));
  }, []);

  const applyShiftAttendance = useCallback((d: ShiftAttendanceData, isBaseline: boolean) => {
    const flat = d.shifts.flatMap((s) => s.workers);
    const next = new Map(flat.map((w) => [w.assignment_id, { clock_in: w.clock_in, clock_out: w.clock_out }]));
    if (isBaseline) {
      prevPunchesRef.current = next;
      setShiftData(d);
      return;
    }
    const prev = prevPunchesRef.current;
    if (!prev) {
      prevPunchesRef.current = next;
      setShiftData(d);
      return;
    }
    const newBanners: LiveBanner[] = [];
    for (const w of flat) {
      const old = prev.get(w.assignment_id);
      if (!old) continue;
      if (!old.clock_in && w.clock_in) {
        newBanners.push({
          id: `in-${w.assignment_id}-${w.clock_in}`,
          kind: "in",
          worker_name: w.worker_name || "—",
          timeLabel: fmtLiveIso(w.clock_in),
        });
      }
      if (!old.clock_out && w.clock_out) {
        newBanners.push({
          id: `out-${w.assignment_id}-${w.clock_out}`,
          kind: "out",
          worker_name: w.worker_name || "—",
          timeLabel: fmtLiveIso(w.clock_out),
        });
      }
    }
    prevPunchesRef.current = next;
    setShiftData(d);
    if (newBanners.length) {
      setBanners((b) => [...newBanners, ...b].slice(0, 8));
    }
  }, []);

  const fetchShiftAttendance = useCallback(
    async (jobId: number, isBaseline: boolean) => {
      setErr(null);
      try {
        const d = await api<ShiftAttendanceData>(`/client/job-orders/${jobId}/shift-attendance`);
        applyShiftAttendance(d, isBaseline);
      } catch (e) {
        setErr(String(e));
        setShiftData(null);
        prevPunchesRef.current = null;
      }
    },
    [applyShiftAttendance],
  );

  function selectJob(jobId: number) {
    setSelectedJobId(jobId);
    setShiftData(null);
    prevPunchesRef.current = null;
    setBanners([]);
    setErr(null);
    void fetchShiftAttendance(jobId, true);
  }

  useEffect(() => {
    if (selectedJobId == null || shiftData === null) return;
    const id = window.setInterval(() => void fetchShiftAttendance(selectedJobId, false), 12000);
    const onVis = () => {
      if (document.visibilityState === "visible" && selectedJobId != null) void fetchShiftAttendance(selectedJobId, false);
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [selectedJobId, shiftData, fetchShiftAttendance]);

  const selectedTitle = selectedJobId != null ? jobs.find((j) => j.id === selectedJobId)?.title : null;

  return (
    <div>
      <h1 className="font-headline text-3xl md:text-4xl font-extrabold text-on-surface tracking-tight mb-2">{t("live_attendance")}</h1>
      <p className="text-on-surface-variant text-sm mb-8">{t("client_live_intro")}</p>
      {jobsErr && <p className="mb-4 text-sm text-error">{jobsErr}</p>}
      {selectedJobId == null && (
        <>
          <p className="text-sm font-semibold text-on-surface mb-3">{t("client_live_select_job")}</p>
          {jobs.length === 0 && !jobsErr && <p className="text-on-surface-variant text-sm">{t("client_live_no_jobs")}</p>}
          <ul className="grid gap-3 sm:grid-cols-2">
            {jobs.map((j) => (
              <li key={j.id}>
                <button
                  type="button"
                  onClick={() => selectJob(j.id)}
                  className="w-full text-left rounded-2xl border border-outline-variant/25 bg-surface-container-lowest p-4 hover:border-primary/40 transition-colors"
                >
                  <p className="font-headline font-bold text-on-surface">{j.title}</p>
                  <p className="text-xs text-on-surface-variant mt-1">#{j.id}</p>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {selectedJobId != null && (
        <>
          <button
            type="button"
            className="mb-4 text-sm font-semibold text-primary hover:underline"
            onClick={() => {
              setSelectedJobId(null);
              setShiftData(null);
              prevPunchesRef.current = null;
              setBanners([]);
              setErr(null);
            }}
          >
            {t("client_live_back_jobs")}
          </button>
          <p className="text-on-surface font-semibold mb-2">
            {selectedTitle || `Job #${selectedJobId}`}
          </p>
          {banners.length > 0 && (
            <div className="mb-4 space-y-2">
              {banners.map((b) => (
                <div
                  key={b.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-primary/30 bg-primary-container/25 px-4 py-3 text-sm text-on-surface"
                >
                  <p>
                    <span className="font-semibold">{b.worker_name}</span>{" "}
                    {b.kind === "in" ? t("live_banner_clock_in") : t("live_banner_clock_out")}{" "}
                    <span className="text-on-surface-variant">{b.timeLabel}</span>
                  </p>
                  <button
                    type="button"
                    className="text-xs font-semibold text-primary hover:underline"
                    onClick={() => setBanners((prev) => prev.filter((x) => x.id !== b.id))}
                  >
                    {t("live_banner_dismiss")}
                  </button>
                </div>
              ))}
            </div>
          )}
          {err && <p className="mb-4 text-sm text-error">{err}</p>}
          {shiftData &&
            shiftData.shifts.map((block) => (
              <div key={block.shift_id} className="mb-8">
                <h2 className="text-sm font-bold uppercase tracking-wide text-on-surface-variant mb-2">
                  {t("client_live_shift_heading")}: {fmtLiveIso(block.starts_at)} – {fmtLiveIso(block.ends_at)}
                </h2>
                {block.workers.length === 0 ? (
                  <p className="text-on-surface-variant text-sm">{t("client_live_no_workers_shift")}</p>
                ) : (
                  <div className="overflow-x-auto rounded-2xl border border-outline-variant/25 bg-surface-container-lowest">
                    <table className="w-full min-w-[800px] text-left text-sm text-on-surface">
                      <thead className="border-b border-outline-variant/20 bg-surface-container-low text-xs uppercase tracking-wide text-on-surface-variant">
                        <tr>
                          <th className="px-4 py-3 font-semibold">Worker</th>
                          <th className="px-4 py-3 font-semibold">{t("live_worker_scheduled")}</th>
                          <th className="px-4 py-3 font-semibold">{t("live_worker_clock_in")}</th>
                          <th className="px-4 py-3 font-semibold">{t("live_worker_clock_out")}</th>
                          <th className="px-4 py-3 font-semibold">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {block.workers.map((w) => (
                          <tr key={w.assignment_id} className="border-b border-outline-variant/10 last:border-0">
                            <td className="px-4 py-3 font-medium">{w.worker_name || "—"}</td>
                            <td className="px-4 py-3 text-on-surface-variant">{fmtLiveIso(w.scheduled_start)}</td>
                            <td className="px-4 py-3 text-on-surface-variant">{fmtLiveIso(w.clock_in)}</td>
                            <td className="px-4 py-3 text-on-surface-variant">{fmtLiveIso(w.clock_out)}</td>
                            <td className="px-4 py-3">
                              <LiveWorkerStatusCells w={w} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ))}
        </>
      )}
    </div>
  );
}

type TalentAgentListItem = { id: number; username: string | null; display_name?: string | null };

export function ClientTalentAgentPage() {
  const { t } = useTranslation();
  const [ctx, setCtx] = useState<ClientContractContext | null>(null);
  const [agents, setAgents] = useState<TalentAgentListItem[]>([]);
  const [selectedTaId, setSelectedTaId] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function reload() {
    setErr(null);
    try {
      const [c, a] = await Promise.all([
        api<ClientContractContext>("/ta-contracts/client/context"),
        api<TalentAgentListItem[]>("/ta-contracts/directory/talent-agents"),
      ]);
      setCtx(c);
      setAgents(a);
    } catch (e) {
      setErr(String(e));
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  async function submitRequest() {
    if (!selectedTaId) return;
    setErr(null);
    setMsg(null);
    try {
      await api("/ta-contracts/client/request", {
        method: "POST",
        json: { ta_user_id: selectedTaId, message: null },
      });
      setMsg(t("client_ta_request_sent"));
      await reload();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function cancelPending(contractId: number) {
    setErr(null);
    try {
      await api(`/ta-contracts/client/pending/${contractId}/cancel`, { method: "POST" });
      await reload();
    } catch (e) {
      setErr(String(e));
    }
  }

  return (
    <div>
      <h1 className="font-headline text-3xl md:text-4xl font-extrabold text-on-surface tracking-tight mb-2">
        {t("client_ta_page_title")}
      </h1>
      <p className="text-on-surface-variant text-sm mb-8 max-w-2xl">{t("client_ta_page_intro")}</p>
      {err && <p className="text-sm text-error mb-4">{err}</p>}
      {msg && <p className="text-sm text-primary mb-4">{msg}</p>}
      {ctx && (
        <div className="rounded-2xl border border-outline-variant/25 bg-surface-container-lowest p-5 mb-8 text-sm text-on-surface">
          <p className="font-semibold mb-2">{t("client_ta_status_heading")}</p>
          {ctx.has_ta_payroll ? (
            <p>
              {t("client_ta_status_active", {
                name: formatClientTaName(ctx.active_ta_display_name, ctx.active_ta_username, ctx.active_ta_user_id),
                id: ctx.active_ta_user_id ?? "—",
              })}
            </p>
          ) : (
            <p>{t("client_ta_status_independent")}</p>
          )}
          {ctx.pending_contracts.length > 0 && (
            <ul className="mt-3 space-y-2">
              {ctx.pending_contracts.map((p) => (
                <li key={p.id} className="flex flex-wrap items-center gap-2 text-on-surface-variant">
                  <span>
                    {t("client_ta_pending_row", {
                      id: p.id,
                      ta: formatClientTaName(p.ta_display_name, p.ta_username, p.ta_user_id),
                    })}
                  </span>
                  <button
                    type="button"
                    className="text-xs font-semibold text-error underline"
                    onClick={() => void cancelPending(p.id)}
                  >
                    {t("client_ta_cancel_request")}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {!ctx?.has_ta_payroll && (
        <div className="rounded-2xl border border-outline-variant/25 bg-surface-container-lowest p-5 max-w-lg">
          <label className="block text-sm font-medium mb-2">{t("client_ta_select_label")}</label>
          <select
            className="w-full rounded-xl border border-outline-variant/30 bg-surface-container-low px-3 py-2 text-sm mb-4"
            value={selectedTaId ?? ""}
            onChange={(e) => setSelectedTaId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">{t("client_ta_select_placeholder")}</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {formatClientTaName(a.display_name, a.username, a.id)} (#{a.id})
              </option>
            ))}
          </select>
          <button
            type="button"
            className="glass-cta text-white px-5 py-2.5 rounded-xl text-sm font-bold"
            disabled={!selectedTaId}
            onClick={() => void submitRequest()}
          >
            {t("client_ta_request_button")}
          </button>
        </div>
      )}
    </div>
  );
}
