import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, getRole, getToken, signOut } from "./api";
import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
import WorkerApp from "./pages/worker/WorkerApp";
import WorkerSignupPage from "./pages/worker/WorkerSignupPage";
import WorkerWelcomePage from "./pages/worker/WorkerWelcomePage";
import { JobOrderDetailView } from "./components/jobOrder/JobOrderDetailView";
import {
  ClientLayout,
  ClientJobHistoryPage,
  ClientJobsList,
  ClientNewJob,
  ClientTimesheetsPage,
  ClientLivePage,
  ClientTalentAgentPage,
} from "./pages/client/ClientApp";
import { ClientAnalyticsPage } from "./pages/client/ClientAnalyticsPage";
import ClientSignupPage from "./pages/client/ClientSignupPage";
import ClientOnboardingPage from "./pages/client/ClientOnboardingPage";
import { ClientProfilePage } from "./pages/client/ClientProfilePage";
import { TaPortalLayout } from "./components/ta/TaPortalLayout";
import {
  TaJobsList,
  TaNewJobPage,
  TaExportsPage,
  TaTimesheetsPage,
  TaClientsPage,
  TaProfilePage,
  TaWorkersPage,
} from "./pages/ta/TaApp";
import { AdminPersonasPage } from "./pages/ta/AdminPersonasPage";
import { AdminReportsPage } from "./pages/ta/AdminReportsPage";
import { TaAnalyticsPage } from "./pages/ta/TaAnalyticsPage";
import NotificationsPage from "./components/portal/NotificationsPage";
import JobOrderMessagingPage from "./pages/messaging/JobOrderMessagingPage";

function GuardWorker({ children }: { children: React.ReactNode }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  if (getRole() !== "worker") return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function GuardTa({ children }: { children: React.ReactNode }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  const r = getRole();
  if (r !== "ta" && r !== "admin") return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function GuardClient({ children }: { children: React.ReactNode }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  if (getRole() !== "client") return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function ClientOnboardingGuard({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<"loading" | "ready" | "onboarding">("loading");
  useEffect(() => {
    if (!getToken() || getRole() !== "client") {
      setState("onboarding");
      return;
    }
    api<{ onboarding_completed: boolean }>("/client/me/profile")
      .then((p) => setState(p.onboarding_completed ? "ready" : "onboarding"))
      .catch(() => setState("onboarding"));
  }, []);
  if (state === "loading") return <div className="p-6 text-sm text-on-surface-variant">Loading…</div>;
  if (state === "onboarding") return <Navigate to="/client/onboarding" replace />;
  return <>{children}</>;
}

function ClientOnboardingOnly() {
  const [state, setState] = useState<"loading" | "allow" | "done">("loading");
  useEffect(() => {
    if (!getToken() || getRole() !== "client") {
      setState("allow");
      return;
    }
    api<{ onboarding_completed: boolean }>("/client/me/profile")
      .then((p) => setState(p.onboarding_completed ? "done" : "allow"))
      .catch(() => setState("allow"));
  }, []);
  if (state === "loading") return <div className="p-6 text-sm text-on-surface-variant">Loading…</div>;
  if (state === "done") return <Navigate to="/client" replace />;
  return <ClientOnboardingPage />;
}

/** Public marketing home; signed-in users go straight to their portal. */
function HomeOrDashboard() {
  const t = getToken();
  const r = getRole();
  if (t && r === "worker") return <Navigate to="/worker" replace />;
  if (t && r === "client") return <Navigate to="/client" replace />;
  if (t && (r === "ta" || r === "admin")) return <Navigate to="/ta" replace />;
  return <HomePage />;
}

function useWorkerPortalFullBleed(): boolean {
  const { pathname } = useLocation();
  if (pathname.startsWith("/worker/welcome") || pathname.startsWith("/worker/signup")) return false;
  return pathname === "/worker" || pathname.startsWith("/worker/");
}

function useClientPortalFullBleed(): boolean {
  const { pathname } = useLocation();
  return pathname === "/client" || pathname.startsWith("/client/");
}

function useTaPortalFullBleed(): boolean {
  const { pathname } = useLocation();
  return pathname === "/ta" || pathname.startsWith("/ta/");
}

export default function App() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const workerPortal = useWorkerPortalFullBleed();
  const clientPortal = useClientPortalFullBleed();
  const taPortal = useTaPortalFullBleed();
  const standaloneAuthLayout =
    pathname === "/" ||
    pathname === "/login" ||
    pathname === "/worker/signup" ||
    pathname === "/client/signup" ||
    pathname === "/client/onboarding" ||
    pathname.startsWith("/worker/welcome");

  const routes = (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/worker/welcome" element={<WorkerWelcomePage />} />
      <Route path="/worker/signup" element={<WorkerSignupPage />} />
      <Route path="/client/signup" element={<ClientSignupPage />} />
      <Route path="/client/onboarding" element={<ClientOnboardingOnly />} />
      <Route
        path="/worker/*"
        element={
          <GuardWorker>
            <WorkerApp />
          </GuardWorker>
        }
      />
      <Route
        path="/ta"
        element={
          <GuardTa>
            <TaPortalLayout />
          </GuardTa>
        }
      >
        <Route index element={getRole() === "admin" ? <Navigate to="/ta/workers" replace /> : <TaJobsList />} />
        <Route path="profile" element={<TaProfilePage />} />
        <Route path="workers" element={<TaWorkersPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="new" element={<TaNewJobPage />} />
        <Route path="analytics" element={<TaAnalyticsPage />} />
        <Route path="exports" element={<TaExportsPage />} />
        <Route path="timesheets" element={<TaTimesheetsPage />} />
        <Route path="clients" element={getRole() === "admin" ? <AdminPersonasPage role="client" /> : <TaClientsPage />} />
        <Route path="talent-agents" element={<AdminPersonasPage role="ta" />} />
        <Route path="reports" element={<AdminReportsPage />} />
        <Route path="messaging" element={<JobOrderMessagingPage portalBase="/ta" />} />
        <Route path="messaging/:jobId" element={<JobOrderMessagingPage portalBase="/ta" />} />
        <Route path=":jobId" element={<JobOrderDetailView variant="ta" basePath="/ta" />} />
      </Route>
      <Route
        path="/client"
        element={
          <GuardClient>
            <ClientOnboardingGuard>
              <ClientLayout />
            </ClientOnboardingGuard>
          </GuardClient>
        }
      >
        <Route index element={<ClientJobsList />} />
        <Route path="analytics" element={<ClientAnalyticsPage />} />
        <Route path="profile" element={<ClientProfilePage />} />
        <Route path="job-history" element={<ClientJobHistoryPage />} />
        <Route path="new" element={<ClientNewJob />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="timesheets" element={<ClientTimesheetsPage />} />
        <Route path="ta-contract" element={<ClientTalentAgentPage />} />
        <Route path="live" element={<ClientLivePage />} />
        <Route path="messaging" element={<JobOrderMessagingPage portalBase="/client" />} />
        <Route path="messaging/:jobId" element={<JobOrderMessagingPage portalBase="/client" />} />
        <Route path=":jobId" element={<JobOrderDetailView variant="client" basePath="/client" />} />
      </Route>
      <Route path="/" element={<HomeOrDashboard />} />
    </Routes>
  );

  if (workerPortal || clientPortal || taPortal || standaloneAuthLayout) {
    return routes;
  }

  return (
    <div className="min-h-screen bg-white text-black">
      <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-black/10 bg-white px-4 py-3">
        <button
          type="button"
          className="text-left font-semibold text-black"
          onClick={() => navigate("/")}
        >
          {t("app_title")}
        </button>
        <div className="flex items-center gap-2">
          <label className="sr-only text-xs text-[#4b4b4b]">{t("language")}</label>
          <select
            className="rounded-pill border border-black bg-white px-3 py-1 text-sm"
            value={i18n.language.startsWith("zh") ? "zh" : "en"}
            onChange={(e) => {
              const lng = e.target.value;
              i18n.changeLanguage(lng);
              localStorage.setItem("locale", lng);
            }}
          >
            <option value="en">English</option>
            <option value="zh">简体中文</option>
          </select>
          {getToken() && (
            <button
              type="button"
              className="text-xs text-[#4b4b4b] underline"
              onClick={() => {
                void signOut();
                navigate("/");
              }}
            >
              Log out
            </button>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">{routes}</main>
    </div>
  );
}
