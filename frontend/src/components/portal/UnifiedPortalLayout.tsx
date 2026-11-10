import type { MouseEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { api, signOut } from "../../api";

function MatIcon({ name, className = "" }: { name: string; className?: string }) {
  return <span className={`material-symbols-outlined ${className}`.trim()} aria-hidden>{name}</span>;
}

type SideItem = {
  to: string;
  labelKey: string;
  icon: string;
  end?: boolean;
};

type UnifiedPortalLayoutProps = {
  role: "worker" | "client" | "ta";
  roleInitial: string;
  brandTitle: string;
  brandSubtitleKey?: string;
  sideNav: SideItem[];

  primaryCta: {
    to: string;
    /** Used for role=worker where the clock page must preserve `?assignment=` query. */
    getHref?: (pathname: string, search: string) => string;
    label: string;
  };

  floatingCta: {
    to: string;
    ariaLabelKey: string;
  };

  asideCta: {
    to: string;
    labelKey: string;
  };

  children: ReactNode;
};

type PortalNotification = {
  id: number;
  title: string;
  body: string;
  read_at?: string | null;
  created_at?: string | null;
  payload?: Record<string, unknown>;
};

const sidebarInactive =
  "mx-3 flex items-center gap-3 text-[#4b4b4b] hover:text-black px-4 py-3 rounded-2xl";
const sidebarActive =
  "mx-3 flex items-center gap-3 bg-black text-white rounded-2xl shadow-card px-4 py-3";

function SideNavLink({
  to,
  end,
  icon,
  children,
  collapsed,
}: {
  to: string;
  end?: boolean;
  icon: string;
  children: ReactNode;
  collapsed: boolean;
}) {
  const inactiveClass = collapsed
    ? `${sidebarInactive} md:mx-0 md:justify-center md:gap-0 md:px-0 md:rounded-none`
    : sidebarInactive;
  const activeClass = collapsed
    ? `${sidebarActive} md:mx-0 md:justify-center md:gap-0 md:px-0 md:rounded-none`
    : sidebarActive;

  return (
    <NavLink to={to} end={end} className={({ isActive }) => (isActive ? activeClass : inactiveClass)}>
      <MatIcon name={icon} />
      <span className={collapsed ? "md:hidden" : ""}>{children}</span>
    </NavLink>
  );
}

export default function UnifiedPortalLayout({
  role,
  roleInitial,
  brandTitle,
  brandSubtitleKey,
  sideNav,
  primaryCta,
  floatingCta,
  asideCta,
  children,
}: UnifiedPortalLayoutProps) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notifications, setNotifications] = useState<PortalNotification[]>([]);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [notificationsError, setNotificationsError] = useState<string | null>(null);
  const [notificationsClearing, setNotificationsClearing] = useState(false);
  const [darkMode, setDarkMode] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("portal-dark-mode") === "1";
  });
  const [themeMorphing, setThemeMorphing] = useState(false);
  const lastSeenMaxNotificationIdRef = useRef(0);
  const themeMorphTimerRef = useRef<number | null>(null);

  const primaryHref = useMemo(() => {
    if (primaryCta.getHref) return primaryCta.getHref(location.pathname, location.search);
    return primaryCta.to;
  }, [location.pathname, location.search, primaryCta]);

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  const loadNotifications = useCallback(async () => {
    setNotificationsLoading(true);
    setNotificationsError(null);
    try {
      setNotifications(await api<PortalNotification[]>("/notifications"));
    } catch (e) {
      setNotificationsError(String(e));
    } finally {
      setNotificationsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadNotifications();
  }, [loadNotifications]);

  useEffect(() => {
    if (!notificationsOpen) return;
    const maxId = notifications.length ? Math.max(...notifications.map((n) => n.id)) : 0;
    lastSeenMaxNotificationIdRef.current = maxId;
  }, [notificationsOpen, notifications]);

  useEffect(() => {
    const onFocus = () => {
      if (!notificationsOpen) void loadNotifications();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [notificationsOpen, loadNotifications]);

  const unreadCount = useMemo(() => notifications.filter((n) => !n.read_at).length, [notifications]);

  const showNewNotificationsBanner =
    !notificationsOpen &&
    notifications.some((n) => !n.read_at && n.id > lastSeenMaxNotificationIdRef.current);

  function toggleNotifications() {
    setNotificationsOpen((open) => {
      const next = !open;
      if (next) void loadNotifications();
      return next;
    });
  }

  function openNotificationsFromBanner() {
    setNotificationsOpen(true);
    void loadNotifications();
  }

  useEffect(() => {
    window.localStorage.setItem("portal-dark-mode", darkMode ? "1" : "0");
  }, [darkMode]);

  useEffect(() => {
    return () => {
      if (themeMorphTimerRef.current !== null) window.clearTimeout(themeMorphTimerRef.current);
    };
  }, []);

  function toggleTheme(event: MouseEvent<HTMLButtonElement>) {
    event.currentTarget.blur();
    setThemeMorphing(true);
    setDarkMode((v) => !v);
    if (themeMorphTimerRef.current !== null) window.clearTimeout(themeMorphTimerRef.current);
    themeMorphTimerRef.current = window.setTimeout(() => setThemeMorphing(false), 1100);
  }

  async function clearAllNotifications() {
    if (notifications.length === 0 || notificationsClearing) return;
    setNotificationsClearing(true);
    setNotificationsError(null);
    try {
      await api("/notifications", { method: "DELETE" });
      setNotifications([]);
      lastSeenMaxNotificationIdRef.current = 0;
    } catch (e) {
      setNotificationsError(String(e));
    } finally {
      setNotificationsClearing(false);
    }
  }

  function formatNotificationTime(iso: string | null | undefined): string {
    if (!iso) return "";
    try {
      return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
    } catch {
      return iso;
    }
  }

  function handleNotificationClick(notification: PortalNotification) {
    if (role === "worker" && notification.payload?.type === "timesheet_disputed") {
      navigate("/worker/earnings");
      setNotificationsOpen(false);
    }
  }

  const sidebarSubtitle = brandSubtitleKey ? t(brandSubtitleKey) : "";
  const asideWidthClass = sidebarCollapsed ? "md:w-20" : "md:w-64";
  const mainPaddingLeftClass = sidebarCollapsed ? "md:pl-[6rem]" : "md:pl-[17rem]";

  return (
    <div className={`portal-theme-root ${darkMode ? "portal-theme-dark" : ""} min-h-screen overflow-x-hidden bg-white font-body text-black`}>
      <header className="fixed left-0 right-0 top-0 z-50 flex h-16 w-full items-center justify-between border-b border-black/10 bg-white px-4 antialiased md:px-8">
        <div className="flex min-w-0 flex-1 items-center gap-4 md:gap-8">
          <button
            type="button"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-black hover:bg-[#efefef] md:hidden"
            aria-label="Open menu"
            onClick={() => setMenuOpen(true)}
          >
            <MatIcon name="menu" className="text-2xl" />
          </button>
          <span className="truncate text-xl font-bold tracking-tight text-black">MCI Workforce</span>
        </div>

        <div className="flex shrink-0 items-center gap-2 md:gap-4">
          <button
            type="button"
            className="theme-toggle-btn relative overflow-hidden rounded-pill border border-black bg-white px-3 py-1.5 text-xs font-semibold text-black transition-all hover:bg-[#efefef] active:scale-95"
            aria-label={darkMode ? "Switch to light mode" : "Switch to dark mode"}
            onClick={toggleTheme}
          >
            <span className="relative z-[1] inline-flex items-center gap-1.5">
              <MatIcon name={darkMode ? "light_mode" : "dark_mode"} className="text-base" />
              {darkMode ? "Light" : "Dark"}
            </span>
          </button>
          <label className="sr-only">{t("language")}</label>
          <select
            className="max-w-[5.5rem] rounded-pill border border-black bg-white px-2 py-1.5 text-xs text-black"
            value={i18n.language.startsWith("zh") ? "zh" : "en"}
            onChange={(e) => {
              const lng = e.target.value;
              i18n.changeLanguage(lng);
              localStorage.setItem("locale", lng);
            }}
          >
            <option value="en">EN</option>
            <option value="zh">中文</option>
          </select>
          <div className="relative hidden sm:block">
            <button
              type="button"
              className="rounded-full p-2 text-black transition-colors hover:bg-[#efefef] active:scale-95"
              aria-label={t("notifications_title")}
              onClick={toggleNotifications}
            >
              <MatIcon name="notifications" />
            </button>
            {unreadCount > 0 && (
              <span className="absolute -right-1 -top-1 min-w-5 rounded-full bg-black px-1.5 py-0.5 text-center text-[10px] font-bold leading-none text-white">
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            )}
          </div>
          <NavLink
            to={primaryHref}
            className="rounded-pill bg-black px-4 py-2 text-sm font-semibold text-white shadow-card transition-colors hover:bg-black/90 active:scale-95 md:px-6"
          >
            {primaryCta.label}
          </NavLink>
          <div className="ml-1 hidden h-10 w-10 overflow-hidden rounded-full border-2 border-black sm:block">
            <div className="flex h-full w-full items-center justify-center bg-[#efefef] text-sm font-bold text-black">
              {roleInitial}
            </div>
          </div>
        </div>
      </header>

      {showNewNotificationsBanner && (
        <button
          type="button"
          className="fixed top-16 right-4 z-[55] hidden w-[22rem] max-w-[calc(100vw-2rem)] items-center justify-center gap-2 rounded-b-xl border border-black border-t-0 bg-black px-4 py-2.5 text-center text-sm font-semibold text-white shadow-floating sm:flex"
          aria-live="polite"
          onClick={openNotificationsFromBanner}
        >
          <MatIcon name="notifications" className="text-lg" />
          {t("notifications_new_banner")}
        </button>
      )}

      {notificationsOpen && (
        <>
          <button
            type="button"
            className="fixed inset-0 z-40 bg-transparent"
            aria-label={t("ta_timesheet_detail_close")}
            onClick={() => setNotificationsOpen(false)}
          />
          <section className="fixed right-4 top-20 z-[70] w-[22rem] max-w-[calc(100vw-2rem)] rounded-featured border border-black/20 bg-white p-4 shadow-medium">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-headline text-base font-bold text-black">{t("notifications_title")}</h2>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  disabled={notificationsLoading || notificationsClearing || notifications.length === 0}
                  className="rounded-pill px-2 py-1 text-xs font-semibold text-black hover:bg-[#efefef] disabled:pointer-events-none disabled:opacity-40"
                  onClick={() => void clearAllNotifications()}
                >
                  {t("notifications_clear_all")}
                </button>
                <button
                  type="button"
                  className="rounded-pill p-1 text-[#4b4b4b] hover:bg-[#efefef]"
                  aria-label={t("ta_timesheet_detail_close")}
                  onClick={() => setNotificationsOpen(false)}
                >
                  <MatIcon name="close" />
                </button>
              </div>
            </div>
            {notificationsLoading ? <p className="text-sm text-[#4b4b4b]">{t("notifications_loading")}</p> : null}
            {notificationsError ? <p className="text-sm text-black">{notificationsError}</p> : null}
            {!notificationsLoading && !notificationsError && notifications.length === 0 ? (
              <p className="text-sm text-[#4b4b4b]">{t("notifications_empty")}</p>
            ) : null}
            {!notificationsLoading && !notificationsError && notifications.length > 0 ? (
              <div className="max-h-[24rem] space-y-2 overflow-y-auto pr-1">
                {notifications.map((notification) => (
                  <button
                    key={notification.id}
                    type="button"
                    className="w-full rounded-card border border-black/10 bg-[#f3f3f3] p-3 text-left transition-colors hover:bg-[#efefef]"
                    onClick={() => handleNotificationClick(notification)}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <p className="font-semibold text-black">{notification.title}</p>
                      {!notification.read_at && <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-black" />}
                    </div>
                    <p className="mt-1 text-sm text-[#4b4b4b]">{notification.body}</p>
                    {notification.created_at ? (
                      <p className="mt-2 text-[11px] uppercase tracking-wide text-[#afafaf]">
                        {formatNotificationTime(notification.created_at)}
                      </p>
                    ) : null}
                  </button>
                ))}
              </div>
            ) : null}
          </section>
        </>
      )}

      {menuOpen && (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-black/40 md:hidden"
          aria-label="Close menu"
          onClick={() => setMenuOpen(false)}
        />
      )}

      <aside
        className={`fixed left-0 top-0 z-40 flex h-screen w-64 flex-col border-r border-black/10 bg-[#f3f3f3] pt-20 text-sm font-medium transition-all duration-200 ${asideWidthClass} md:translate-x-0 ${
          menuOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="mb-8 flex items-start justify-between gap-3 px-6 md:px-4">
          <div className="min-w-0">
            <h2 className={`text-xl font-black text-black ${sidebarCollapsed ? "md:hidden" : ""}`}>{brandTitle}</h2>
          {brandSubtitleKey && (
            <p className={`text-[10px] uppercase tracking-widest text-[#4b4b4b] opacity-70 ${sidebarCollapsed ? "md:hidden" : ""}`}>
              {sidebarSubtitle}
            </p>
          )}
          </div>

          <button
            type="button"
            className="hidden h-9 w-9 items-center justify-center rounded-pill text-black hover:bg-[#efefef] active:scale-95 md:flex"
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => setSidebarCollapsed((v) => !v)}
          >
            <MatIcon name={sidebarCollapsed ? "chevron_right" : "chevron_left"} />
          </button>
        </div>
        <nav className="flex flex-1 flex-col space-y-1 overflow-y-auto">
          {sideNav.map((item) => (
            <SideNavLink key={item.to} to={item.to} end={item.end} icon={item.icon} collapsed={sidebarCollapsed}>
              {t(item.labelKey)}
            </SideNavLink>
          ))}
        </nav>
        <div className="border-t border-black/10 px-6 py-6 md:px-3">
          <NavLink
            to={asideCta.to}
            className="mb-4 flex w-full items-center justify-center gap-2 rounded-pill bg-black py-3 font-bold text-white transition-all hover:bg-black/90"
          >
            <MatIcon name="add_circle" className="text-lg" />
            <span className={sidebarCollapsed ? "md:hidden" : ""}>{t(asideCta.labelKey)}</span>
          </NavLink>
          <div className="space-y-1">
            <button
              type="button"
              className="flex w-full items-center gap-3 py-2 text-[#4b4b4b] transition-colors hover:text-black md:justify-center md:gap-0"
              onClick={() => {
                void signOut();
                navigate("/");
              }}
            >
              <MatIcon name="logout" />
              <span className={sidebarCollapsed ? "md:hidden" : ""}>Log out</span>
            </button>
          </div>
        </div>
      </aside>

      <main className={`flex min-h-screen flex-col pt-20 transition-all duration-200 ${mainPaddingLeftClass}`}>
        <div className="flex-1">
          {children}
        </div>
        <footer className="mt-auto w-full bg-black py-8 text-xs tracking-wide text-white">
          <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-8 md:flex-row">
            <div>
              <span className="mr-4 font-manrope font-bold text-white">MCI Workforce</span>
              <span className="text-[#afafaf]">© {new Date().getFullYear()} MCI Workforce Management. All rights reserved.</span>
            </div>
            <nav className="flex flex-wrap justify-center gap-6">
              <a className="text-[#afafaf] underline underline-offset-4 hover:text-white" href="#" onClick={(e) => e.preventDefault()}>
                Privacy Policy
              </a>
              <a className="text-[#afafaf] underline underline-offset-4 hover:text-white" href="#" onClick={(e) => e.preventDefault()}>
                Terms of Service
              </a>
              <a className="text-[#afafaf] underline underline-offset-4 hover:text-white" href="#" onClick={(e) => e.preventDefault()}>
                Compliance
              </a>
              <a className="text-[#afafaf] underline underline-offset-4 hover:text-white" href="#" onClick={(e) => e.preventDefault()}>
                Support
              </a>
            </nav>
          </div>
        </footer>
      </main>

      <NavLink
        to={floatingCta.to}
        className="fixed bottom-8 right-8 z-50 flex h-16 w-16 translate-y-[2px] items-center justify-center rounded-full bg-white text-black shadow-floating transition-all hover:scale-110 hover:bg-[#f3f3f3] active:scale-95"
        aria-label={t(floatingCta.ariaLabelKey)}
      >
        <MatIcon name="add" className="text-3xl" />
      </NavLink>
      {themeMorphing && (
        <div
          className={`theme-morph-overlay ${darkMode ? "theme-morph-dark" : "theme-morph-light"}`}
          aria-hidden
        />
      )}
    </div>
  );
}

