import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { api, apiUrl, getUserIdFromToken, getValidToken } from "../../api";
import { jobStatusLabelKey } from "../../components/jobOrder/jobOrderStatus";
import {
  decryptChatMessage,
  encryptChatMessage,
  generateRoomKey,
  publicKeyB64,
  unwrapRoomKey,
  wrapRoomKey,
} from "../../crypto/jobChatCrypto";

type PortalBase = "/worker" | "/client" | "/ta";

type ChatJobRow = {
  id: number;
  title: string;
  status: string;
  last_message_at: string | null;
};

type Participant = {
  user_id: number;
  role: string;
  display_name: string;
};

type ParticipantKey = {
  user_id: number;
  x25519_public_key_b64: string;
};

type ChatMsg = {
  id: number;
  sender_user_id: number;
  ciphertext_b64: string;
  iv_b64: string;
  created_at: string;
};

type DecMsg = {
  id: number;
  sender_user_id: number;
  text: string;
  created_at: string;
};

function fmtTime(iso: string) {
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "short" }).format(new Date(iso));
  } catch {
    return iso;
  }
}

export default function JobOrderMessagingPage({ portalBase }: { portalBase: PortalBase }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const params = useParams();
  const jobIdParam = params.jobId ? parseInt(params.jobId, 10) : NaN;
  const selectedJobId = Number.isFinite(jobIdParam) ? jobIdParam : null;

  const myUserId = useMemo(() => getUserIdFromToken(), []);

  const [jobs, setJobs] = useState<ChatJobRow[]>([]);
  const [jobsErr, setJobsErr] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [activeOnly, setActiveOnly] = useState(false);
  const [jobsPanelCollapsed, setJobsPanelCollapsed] = useState(false);
  const [hasNewByJob, setHasNewByJob] = useState<Record<number, boolean>>({});
  const lastMessageAtByJobRef = useRef<Record<number, string | null>>({});
  const hasBootstrappedJobsRef = useRef(false);

  const [participants, setParticipants] = useState<Participant[]>([]);
  const [threadErr, setThreadErr] = useState<string | null>(null);
  const [bootStatus, setBootStatus] = useState<string | null>(null);
  const roomKeyRef = useRef<Uint8Array | null>(null);
  const [, forceRoom] = useState(0);
  const setRoomKey = (k: Uint8Array | null) => {
    roomKeyRef.current = k;
    forceRoom((x) => x + 1);
  };

  const [messages, setMessages] = useState<DecMsg[]>([]);
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionIndex, setMentionIndex] = useState(0);

  const filteredJobs = useMemo(() => {
    let rows = jobs;
    if (activeOnly) {
      rows = rows.filter((j) => j.status !== "completed" && j.status !== "cancelled");
    }
    const q = search.trim().toLowerCase();
    if (q) rows = rows.filter((j) => j.title.toLowerCase().includes(q));
    return rows;
  }, [jobs, activeOnly, search]);

  const loadJobs = useCallback(() => {
    setJobsErr(null);
    void api<ChatJobRow[]>("/job-order-chat")
      .then((nextJobs) => {
        setJobs(nextJobs);
        const prev = lastMessageAtByJobRef.current;
        if (hasBootstrappedJobsRef.current) {
          const changedJobIds = nextJobs
            .filter((j) => (prev[j.id] ?? null) !== (j.last_message_at ?? null))
            .map((j) => j.id);
          if (changedJobIds.length > 0) {
            setHasNewByJob((current) => {
              const out = { ...current };
              for (const jid of changedJobIds) {
                if (selectedJobId != null && jid === selectedJobId) continue;
                out[jid] = true;
              }
              return out;
            });
          }
        } else {
          hasBootstrappedJobsRef.current = true;
        }
        const snapshot: Record<number, string | null> = {};
        for (const j of nextJobs) snapshot[j.id] = j.last_message_at ?? null;
        lastMessageAtByJobRef.current = snapshot;
      })
      .catch((e) => setJobsErr(String(e)));
  }, [selectedJobId]);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  useEffect(() => {
    const id = window.setInterval(() => {
      loadJobs();
    }, 6000);
    return () => window.clearInterval(id);
  }, [loadJobs]);

  const decryptBatch = useCallback(
    async (jobId: number, rows: ChatMsg[], key: Uint8Array): Promise<DecMsg[]> => {
      const out: DecMsg[] = [];
      for (const m of rows) {
        try {
          const text = await decryptChatMessage(key, m.ciphertext_b64, m.iv_b64, jobId);
          out.push({ id: m.id, sender_user_id: m.sender_user_id, text, created_at: m.created_at });
        } catch {
          // Skip undecryptable ciphertext rows in UI.
        }
      }
      return out;
    },
    [],
  );

  const bootstrapThread = useCallback(
    async (jobId: number) => {
      setThreadErr(null);
      setBootStatus(t("messaging_bootstrapping"));
      setRoomKey(null);
      setMessages([]);
      setParticipants([]);

      try {
        const idStatus = await api<{ registered: boolean }>("/job-order-chat/me/identity");
        if (!idStatus.registered) {
          await api("/job-order-chat/me/identity", {
            method: "PUT",
            json: { x25519_public_key_b64: publicKeyB64() },
          });
        }

        const parts = await api<Participant[]>(`/job-order-chat/${jobId}/participants`);
        setParticipants(parts);

        const pkeys = await api<ParticipantKey[]>(`/job-order-chat/${jobId}/participant-keys`);
        const keyByUser = new Map(pkeys.map((k) => [k.user_id, k.x25519_public_key_b64]));
        const wrappable = parts.filter((p) => keyByUser.has(p.user_id));

        let rk: Uint8Array;
        const token = await getValidToken();
        const wrapRes = await fetch(apiUrl(`/job-order-chat/${jobId}/wrappers/me`), {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          cache: "no-store",
        });
        if (wrapRes.status === 404) {
          rk = generateRoomKey();
          for (const p of wrappable) {
            const pub = keyByUser.get(p.user_id);
            if (!pub) continue;
            const wrapped = await wrapRoomKey(rk, pub, jobId);
            await api(`/job-order-chat/${jobId}/wrappers`, {
              method: "PUT",
              json: { target_user_id: p.user_id, wrapped_key_b64: wrapped },
            });
          }
        } else if (!wrapRes.ok) {
          const text = await wrapRes.text();
          throw new Error(text || wrapRes.statusText);
        } else {
          const wrap = (await wrapRes.json()) as { user_id: number; wrapped_key_b64: string };
          rk = await unwrapRoomKey(wrap.wrapped_key_b64, jobId);
        }

        setRoomKey(rk);
        const raw = await api<ChatMsg[]>(`/job-order-chat/${jobId}/messages?limit=80`);
        const dec = await decryptBatch(jobId, raw, rk);
        setMessages(dec);
        setBootStatus(null);
      } catch (e) {
        setBootStatus(null);
        setThreadErr(String(e));
      }
    },
    [decryptBatch, t],
  );

  useEffect(() => {
    if (!selectedJobId) {
      setRoomKey(null);
      setMessages([]);
      setParticipants([]);
      setThreadErr(null);
      setBootStatus(null);
      return;
    }
    setHasNewByJob((prev) => ({ ...prev, [selectedJobId]: false }));
    void bootstrapThread(selectedJobId);
  }, [selectedJobId, bootstrapThread]);

  const maxMsgId = messages.length ? Math.max(...messages.map((m) => m.id)) : 0;

  useEffect(() => {
    if (!selectedJobId || !roomKeyRef.current) return;
    const rk = roomKeyRef.current;
    const currentJobId = selectedJobId;
    const id = window.setInterval(() => {
      void (async () => {
        try {
          const raw = await api<ChatMsg[]>(`/job-order-chat/${currentJobId}/messages?since_id=${maxMsgId}&limit=50`);
          if (raw.length === 0) return;
          const dec = await decryptBatch(currentJobId, raw, rk);
          const hasIncoming = dec.some((m) => m.sender_user_id !== myUserId);
          if (hasIncoming) {
            setHasNewByJob((prev) => ({ ...prev, [currentJobId]: true }));
          }
          setMessages((prev) => {
            const seen = new Set(prev.map((p) => p.id));
            const merged = [...prev];
            for (const m of dec) {
              if (!seen.has(m.id)) merged.push(m);
            }
            merged.sort((a, b) => a.id - b.id);
            return merged;
          });
        } catch {
          /* ignore poll errors */
        }
      })();
    }, 4000);
    return () => window.clearInterval(id);
  }, [selectedJobId, maxMsgId, decryptBatch, myUserId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, selectedJobId]);

  const send = async () => {
    const text = draft.trim();
    const jobId = selectedJobId;
    const rk = roomKeyRef.current;
    if (!text || !jobId || !rk || !myUserId) return;
    setDraft("");
    try {
      const { ciphertextB64, ivB64 } = await encryptChatMessage(rk, text, jobId);
      const created = await api<ChatMsg>(`/job-order-chat/${jobId}/messages`, {
        method: "POST",
        json: { ciphertext_b64: ciphertextB64, iv_b64: ivB64 },
      });
      setMessages((prev) => [
        ...prev,
        { id: created.id, sender_user_id: created.sender_user_id, text, created_at: created.created_at },
      ]);
      void loadJobs();
    } catch (e) {
      setDraft(text);
      setThreadErr(String(e));
    }
  };

  const roleLabel = (role: string) => {
    if (role === "client") return t("messaging_partner_role_client");
    if (role === "ta") return t("messaging_partner_role_ta");
    return t("messaging_partner_role_worker");
  };
  const showThreadNewBanner = selectedJobId != null && Boolean(hasNewByJob[selectedJobId]);

  const mentionOptions = useMemo(() => {
    const q = mentionQuery.trim().toLowerCase();
    const base = participants.filter((p) => p.user_id !== myUserId);
    if (!q) return base;
    return base.filter((p) => p.display_name.toLowerCase().includes(q));
  }, [mentionQuery, participants, myUserId]);

  const syncMentionState = (value: string, caretPos: number) => {
    const before = value.slice(0, caretPos);
    const match = before.match(/(?:^|\s)@([^\s@]*)$/);
    if (!match) {
      setMentionOpen(false);
      setMentionQuery("");
      setMentionIndex(0);
      return;
    }
    setMentionOpen(true);
    setMentionQuery(match[1] ?? "");
    setMentionIndex(0);
  };

  const applyMention = (name: string) => {
    const el = textareaRef.current;
    if (!el) return;
    const caret = el.selectionStart ?? draft.length;
    const before = draft.slice(0, caret);
    const after = draft.slice(caret);
    const atIndex = before.lastIndexOf("@");
    if (atIndex < 0) return;
    const prefix = before.slice(0, atIndex);
    const next = `${prefix}@${name} ${after}`;
    setDraft(next);
    setMentionOpen(false);
    setMentionQuery("");
    requestAnimationFrame(() => {
      const pos = prefix.length + name.length + 2;
      el.focus();
      el.setSelectionRange(pos, pos);
    });
  };

  return (
    <div className="flex min-h-[calc(100vh-5rem)] flex-col bg-surface-container-lowest text-on-surface">
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        {/* Sidebar list */}
        <div
          className={`flex w-full flex-shrink-0 flex-col border-outline-variant/20 bg-surface-container-low md:border-r ${
            jobsPanelCollapsed ? "md:w-[72px]" : "md:w-[min(100%,380px)]"
          }`}
        >
          <div className="border-b border-outline-variant/20 px-4 py-3">
            <div className="flex items-center justify-between gap-2">
              <h1 className={`font-headline text-lg font-bold text-on-surface ${jobsPanelCollapsed ? "md:hidden" : ""}`}>
                {t("messaging_title")}
              </h1>
              <button
                type="button"
                className="hidden rounded-full p-1 text-on-surface-variant hover:bg-surface-container-high md:flex"
                onClick={() => setJobsPanelCollapsed((v) => !v)}
                aria-label={jobsPanelCollapsed ? t("messaging_expand_jobs") : t("messaging_collapse_jobs")}
              >
                <span className="material-symbols-outlined">{jobsPanelCollapsed ? "chevron_right" : "chevron_left"}</span>
              </button>
            </div>
            {!jobsPanelCollapsed && <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => setActiveOnly(false)}
                className={`rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
                  !activeOnly ? "bg-black text-white" : "bg-surface-container-high text-on-surface-variant"
                }`}
              >
                {t("messaging_filter_all")}
              </button>
              <button
                type="button"
                onClick={() => setActiveOnly(true)}
                className={`rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
                  activeOnly ? "bg-black text-white" : "bg-surface-container-high text-on-surface-variant"
                }`}
              >
                {t("messaging_filter_active")}
              </button>
            </div>}
            {!jobsPanelCollapsed && <div className="mt-3 flex items-center gap-2 rounded-xl border border-outline-variant/25 bg-surface-container px-3 py-2">
              <span className="material-symbols-outlined text-on-surface-variant text-lg">search</span>
              <input
                className="min-w-0 flex-1 bg-transparent text-sm text-on-surface outline-none placeholder:text-on-surface-variant"
                placeholder={t("messaging_search_placeholder")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>}
          </div>
          {!jobsPanelCollapsed && jobsErr && (
            <p className="mx-4 my-2 rounded-xl border border-error-container/40 bg-error-container/15 px-3 py-2 text-sm text-on-error-container">
              {jobsErr}
            </p>
          )}
          {!jobsPanelCollapsed && <div className="min-h-0 flex-1 overflow-y-auto">
            {filteredJobs.length === 0 && !jobsErr && (
              <p className="px-4 py-6 text-center text-sm text-on-surface-variant">{t("messaging_no_jobs")}</p>
            )}
            {filteredJobs.map((j) => (
              <button
                key={j.id}
                type="button"
                onClick={() => navigate(`${portalBase}/messaging/${j.id}`)}
                className={`flex w-full gap-3 border-b border-outline-variant/15 px-4 py-3 text-left transition-colors hover:bg-surface-container-high ${
                  selectedJobId === j.id ? "bg-surface-container-highest ring-1 ring-inset ring-primary/25" : ""
                }`}
              >
                <div className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-full bg-primary/15 text-sm font-bold text-primary">
                  {j.title.slice(0, 1).toUpperCase()}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <span className="truncate font-semibold text-on-surface">{j.title}</span>
                    {j.last_message_at && (
                      <span className="flex-shrink-0 text-[11px] text-on-surface-variant">{fmtTime(j.last_message_at)}</span>
                    )}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2">
                    <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-on-surface-variant">
                      {t(jobStatusLabelKey(j.status))}
                    </span>
                    <span className="text-[11px] text-on-surface-variant/80">{t("messaging_encrypted_badge")}</span>
                    {hasNewByJob[j.id] && (
                      <span className="rounded-full bg-[#22C55E] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
                        {t("messaging_new_messages")}
                      </span>
                    )}
                  </div>
                </div>
              </button>
            ))}
          </div>}
        </div>

        {/* Main thread */}
        <div className="flex min-h-[50vh] min-w-0 flex-1 flex-col bg-surface-container-lowest md:min-h-0">
          {!selectedJobId && (
            <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center text-on-surface-variant">
              <span className="material-symbols-outlined text-6xl text-outline-variant/50">forum</span>
              <p className="max-w-sm text-sm">{t("messaging_select_thread")}</p>
            </div>
          )}

          {selectedJobId && (
            <>
              <header className="flex flex-shrink-0 items-center gap-3 border-b border-outline-variant/20 bg-surface-container-low px-4 py-3">
                <button
                  type="button"
                  className="rounded-full p-1 text-on-surface-variant hover:bg-surface-container-high md:hidden"
                  onClick={() => navigate(`${portalBase}/messaging`)}
                  aria-label={t("back")}
                >
                  <span className="material-symbols-outlined">arrow_back</span>
                </button>
                <div className="min-w-0 flex-1">
                  <h2 className="truncate font-headline text-lg font-bold text-on-surface">
                    {jobs.find((x) => x.id === selectedJobId)?.title ?? `Job #${selectedJobId}`}
                  </h2>
                  <p className="truncate text-xs text-on-surface-variant">
                    {t("messaging_participants")}:{" "}
                    {participants.map((p) => `${p.display_name} (${roleLabel(p.role)})`).join(" · ")}
                  </p>
                </div>
              </header>

              {bootStatus && (
                <p className="bg-surface-container-high px-4 py-2 text-center text-sm text-on-surface-variant">{bootStatus}</p>
              )}
              {threadErr && (
                <p className="mx-4 my-2 rounded-xl border border-error-container/40 bg-error-container/15 px-3 py-2 text-center text-sm text-on-error-container">
                  {threadErr}
                </p>
              )}
              {showThreadNewBanner && (
                <button
                  type="button"
                  onClick={() => {
                    if (selectedJobId) setHasNewByJob((prev) => ({ ...prev, [selectedJobId]: false }));
                    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
                  }}
                  className="mx-4 my-2 rounded-xl bg-[#22C55E] px-3 py-2 text-center text-sm font-semibold text-white"
                >
                  {t("messaging_new_messages")}
                </button>
              )}

              <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
                <p className="mx-auto mb-4 max-w-md rounded-xl border border-outline-variant/20 bg-surface-container-low px-3 py-2 text-center text-[11px] leading-relaxed text-on-surface-variant">
                  {t("messaging_e2ee_notice")}
                </p>
                <div className="mx-auto flex max-w-3xl flex-col gap-2.5">
                  {messages.map((m) => {
                    const mine = myUserId != null && m.sender_user_id === myUserId;
                    const sender = participants.find((p) => p.user_id === m.sender_user_id)?.display_name ?? "—";
                    return (
                      <div key={m.id} className={`flex flex-col ${mine ? "items-end" : "items-start"}`}>
                        {!mine && <p className="mb-1 px-2 text-xs font-semibold text-on-surface-variant">{sender}</p>}
                        <div
                          className={`max-w-[85%] rounded-[22px] px-4 py-2.5 text-sm leading-5 shadow-[0_1px_1px_rgba(0,0,0,0.06)] ${
                            mine
                              ? "rounded-br-md bg-[#0A84FF] text-white"
                              : "rounded-bl-md bg-[#E5E5EA] text-[#111111]"
                          }`}
                        >
                          <p className="whitespace-pre-wrap break-words">{m.text}</p>
                          <p className={`mt-1 text-[10px] ${mine ? "text-white/80" : "text-[#6B7280]"}`}>{fmtTime(m.created_at)}</p>
                        </div>
                      </div>
                    );
                  })}
                  <div ref={bottomRef} />
                </div>
              </div>

              <div className="flex-shrink-0 border-t border-outline-variant/20 bg-surface-container-low px-3 py-2">
                <div className="relative mx-auto flex max-w-3xl items-end gap-2">
                  <textarea
                    ref={textareaRef}
                    className="max-h-32 min-h-[44px] flex-1 resize-y rounded-xl border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none placeholder:text-on-surface-variant disabled:opacity-50"
                    placeholder={t("messaging_placeholder_empty")}
                    rows={1}
                    value={draft}
                    disabled={!roomKeyRef.current}
                    onChange={(e) => {
                      const next = e.target.value;
                      setDraft(next);
                      syncMentionState(next, e.target.selectionStart ?? next.length);
                    }}
                    onKeyDown={(e) => {
                      if (mentionOpen && mentionOptions.length > 0) {
                        if (e.key === "ArrowDown") {
                          e.preventDefault();
                          setMentionIndex((prev) => (prev + 1) % mentionOptions.length);
                          return;
                        }
                        if (e.key === "ArrowUp") {
                          e.preventDefault();
                          setMentionIndex((prev) => (prev - 1 + mentionOptions.length) % mentionOptions.length);
                          return;
                        }
                        if (e.key === "Enter" || e.key === "Tab") {
                          e.preventDefault();
                          applyMention(mentionOptions[Math.min(mentionIndex, mentionOptions.length - 1)].display_name);
                          return;
                        }
                        if (e.key === "Escape") {
                          setMentionOpen(false);
                          return;
                        }
                      }
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        void send();
                      }
                    }}
                  />
                  {mentionOpen && mentionOptions.length > 0 && (
                    <div className="absolute bottom-16 left-4 right-16 z-20 max-h-44 overflow-y-auto rounded-xl border border-outline-variant/30 bg-surface-container-high shadow-lg">
                      <p className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant">
                        {t("messaging_mentions_label")}
                      </p>
                      {mentionOptions.map((p, idx) => (
                        <button
                          key={p.user_id}
                          type="button"
                          onClick={() => applyMention(p.display_name)}
                          className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors ${
                            idx === mentionIndex ? "bg-surface-container-highest" : "hover:bg-surface-container-highest/60"
                          }`}
                        >
                          <span className="truncate text-on-surface">{p.display_name}</span>
                          <span className="text-xs text-on-surface-variant">{roleLabel(p.role)}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => void send()}
                    disabled={!roomKeyRef.current || !draft.trim()}
                    className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full bg-black text-white transition-opacity disabled:opacity-40"
                    aria-label={t("messaging_send")}
                  >
                    <span className="material-symbols-outlined text-[22px]">send</span>
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
