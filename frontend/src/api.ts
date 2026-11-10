/**
 * Default `/api` everywhere: the Vite dev proxy, Vercel (rewrites /api/* to the Python function) and the
 * Docker image (FastAPI also accepts the /api prefix). `VITE_API_BASE=""` = bare same-origin paths;
 * an absolute `VITE_API_BASE` = API on another host.
 *
 * Must use direct `import.meta.env.*` access so Vite can replace them at build time;
 * `const m = import.meta; m.env.X` leaves `m.env` undefined in production.
 */
function apiBase(): string {
  const raw = import.meta.env.VITE_API_BASE;
  if (raw === "") return "";
  if (raw != null && raw !== "/") {
    return raw.replace(/\/$/, "");
  }
  return "/api";
}

const BASE = apiBase();

/** Turn FastAPI `{"detail": ...}` JSON into a readable message for the UI. */
export function parseFastApiErrorDetail(body: string): string {
  const raw = body?.trim();
  if (!raw) return "";
  try {
    const j = JSON.parse(raw) as { detail?: unknown };
    const d = j.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d
        .map((x) => {
          if (x && typeof x === "object" && "msg" in x) return String((x as { msg: string }).msg);
          return JSON.stringify(x);
        })
        .join("; ");
    }
    if (d != null && typeof d === "object") return JSON.stringify(d);
  } catch {
    /* not JSON */
  }
  return raw;
}

/** Same origin prefix as `api()` (e.g. `/api` in Vite dev, `` in prod). Use for FormData fetch. */
export function apiUrl(path: string): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${BASE}${normalized}`;
}

export type Role = "worker" | "ta" | "client" | "admin";

/** Session returned by /auth/login, /auth/*-otp/verify and /auth/refresh (a Supabase Auth session). */
export type SessionResponse = {
  access_token: string;
  refresh_token: string;
  expires_at: number;
  role: string;
  user_id: number;
  locale?: string;
};

const K_ACCESS = "mci_token";
const K_REFRESH = "mci_refresh_token";
const K_EXPIRES = "mci_expires_at";
const K_ROLE = "mci_role";
/** Refresh this many seconds before the access token expires. */
const REFRESH_SKEW_S = 60;

function tokenPayload(token: string | null): Record<string, unknown> | null {
  if (!token) return null;
  try {
    const parts = token.split(".");
    if (parts.length < 2) return null;
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    return JSON.parse(atob(padded)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** App claims live in Supabase `app_metadata` (only the backend's service role can write them). */
function appMetadata(token: string | null): Record<string, unknown> {
  const meta = tokenPayload(token)?.app_metadata;
  return meta && typeof meta === "object" ? (meta as Record<string, unknown>) : {};
}

function isRole(r: unknown): r is Role {
  return r === "worker" || r === "ta" || r === "client" || r === "admin";
}

export function getToken(): string | null {
  return localStorage.getItem(K_ACCESS);
}

export function getUserIdFromToken(): number | null {
  const id = appMetadata(getToken()).mci_user_id;
  return typeof id === "number" && Number.isFinite(id) ? id : null;
}

export function getRole(): Role | null {
  const tokenRole = appMetadata(getToken()).mci_role;
  if (isRole(tokenRole)) {
    localStorage.setItem(K_ROLE, tokenRole);
    return tokenRole;
  }
  const r = localStorage.getItem(K_ROLE);
  return isRole(r) ? r : null;
}

export function setSession(s: SessionResponse) {
  localStorage.setItem(K_ACCESS, s.access_token);
  localStorage.setItem(K_REFRESH, s.refresh_token);
  localStorage.setItem(K_EXPIRES, String(s.expires_at));
  localStorage.setItem(K_ROLE, s.role);
}

export function clearSession() {
  for (const k of [K_ACCESS, K_REFRESH, K_EXPIRES, K_ROLE]) localStorage.removeItem(k);
}

/** Revoke the Supabase session (best effort) and clear local state. */
export async function signOut() {
  const token = getToken();
  clearSession();
  if (token) {
    try {
      await fetch(apiUrl("/auth/logout"), { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    } catch {
      /* offline: local session is already gone */
    }
  }
}

let refreshInFlight: Promise<string | null> | null = null;

/** Exchange the refresh token for a new session. Concurrent callers share one request. */
export function refreshSession(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;
  const refresh = localStorage.getItem(K_REFRESH);
  if (!refresh) return Promise.resolve(null);
  refreshInFlight = (async () => {
    try {
      const res = await fetch(apiUrl("/auth/refresh"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
        cache: "no-store",
      });
      if (!res.ok) {
        // Another tab may have rotated the token already; adopt its session if so.
        const latest = localStorage.getItem(K_REFRESH);
        if (latest && latest !== refresh) return getToken();
        if (res.status === 401) clearSession();
        return null;
      }
      const s = (await res.json()) as SessionResponse;
      setSession(s);
      return s.access_token;
    } catch {
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

/** Access token valid for at least REFRESH_SKEW_S more seconds (refreshing first if needed). */
export async function getValidToken(): Promise<string | null> {
  const token = getToken();
  if (!token) return null;
  const exp = Number(localStorage.getItem(K_EXPIRES) || 0);
  if (exp && exp - Date.now() / 1000 > REFRESH_SKEW_S) return token;
  return (await refreshSession()) ?? getToken();
}

export async function api<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string>),
  };
  const isAuthCall = path.startsWith("/auth/");
  const token = isAuthCall ? null : await getValidToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const doFetch = (base: string) =>
    fetch(`${base}${path}`, {
      ...init,
      headers,
      body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
      // Authenticated JSON APIs must not reuse cached GET bodies after mutations (e.g. TA worker directory).
      cache: init.cache ?? "no-store",
    });

  let res = await doFetch(BASE);
  // Fallback: if /api proxy is unavailable in current runtime,
  // retry same-origin paths without prefix (Vite dev or custom hosting).
  if (res.status === 404 && BASE === "/api") {
    res = await doFetch("");
  }

  // Token expired or was revoked between our check and the request: refresh once and retry.
  if (res.status === 401 && token) {
    const fresh = await refreshSession();
    if (fresh && fresh !== token) {
      headers.Authorization = `Bearer ${fresh}`;
      res = await doFetch(BASE);
    }
  }

  if (!res.ok) {
    if (res.status === 401 && !isAuthCall) {
      clearSession();
      if (typeof window !== "undefined" && window.location.pathname !== "/login") {
        window.location.assign("/login");
      }
    }
    const text = await res.text();
    throw new Error(parseFastApiErrorDetail(text) || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
