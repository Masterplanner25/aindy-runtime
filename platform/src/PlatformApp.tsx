import { lazy, type ReactNode } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";

import ErrorBoundary, { RouteErrorBoundary } from "./components/shared/ErrorBoundary";
import { AuthProvider, useAuth } from "@aindy/ui-kit";
import { SystemProvider, useSystem } from "@aindy/ui-kit";
import { FEATURE_FLAGS } from "./api/_routes.js";

import LoginPage from "./components/platform/LoginPage";
import NotAdmin from "./components/platform/NotAdmin";
import PlatformShell from "./components/platform/PlatformShell";

const AgentConsole = lazy(() => import("./components/platform/AgentConsole"));
const FlowEngineConsole = lazy(() => import("./components/platform/FlowEngineConsole"));
const ObservabilityDashboard = lazy(() => import("./components/platform/ObservabilityDashboard"));
const HealthDashboard = lazy(() => import("./components/platform/HealthDashboard"));
const ExecutionConsole = lazy(() => import("./components/platform/ExecutionConsole"));
const AgentApprovalInbox = lazy(() => import("./components/platform/AgentApprovalInbox"));
const AgentRegistry = lazy(() => import("./components/platform/AgentRegistry"));
const RippleTraceViewer = lazy(() => import("./components/platform/RippleTraceViewer"));
const AdminUsersPanel = lazy(() => import("./components/platform/AdminUsersPanel"));
// FR-21 — adopted from the app-side operator SPA; these two drive runtime-owned routes
// (`/platform/webhooks*`, `/platform/queue/dead-letters*`) that this console did not expose.
const WebhooksPanel = lazy(() => import("./components/platform/WebhooksPanel"));
const DeadLetterQueuePanel = lazy(() => import("./components/platform/DeadLetterQueuePanel"));

function platformRoute(name: string, element: ReactNode) {
  return (
    <RouteErrorBoundary name={name} layer="platform" domain={name}>
      {element}
    </RouteErrorBoundary>
  );
}

/**
 * Wraps the platform route tree in an ErrorBoundary that resets on every
 * navigation. Without this, a crash on one screen leaves the outer boundary
 * in error state, poisoning all subsequent in-app navigations until reload.
 */
function PlatformRoutes() {
  const location = useLocation();
  return (
    <ErrorBoundary key={location.pathname} layer="platform">
      <Routes>
        <Route path="/" element={<PlatformHomeRedirect />} />
        <Route element={<PlatformShell />}>
          <Route path="/agent" element={platformRoute("Agent Console", <AgentConsole />)} />
          <Route path="/flows" element={platformRoute("Flow Engine", <FlowEngineConsole />)} />
          <Route path="/observability" element={platformRoute("Observability", <ObservabilityDashboard />)} />
          <Route path="/health" element={platformRoute("Health", <HealthDashboard />)} />
          <Route path="/executions" element={platformRoute("Executions", <ExecutionConsole />)} />
          <Route path="/approvals" element={platformRoute("Approvals", <AgentApprovalInbox />)} />
          <Route path="/registry" element={platformRoute("Registry", <AgentRegistry />)} />
          <Route path="/users" element={platformRoute("Users", <AdminUsersPanel />)} />
          <Route path="/webhooks" element={platformRoute("Webhooks", <WebhooksPanel />)} />
          <Route path="/dead-letters" element={platformRoute("Dead-Letter Queue", <DeadLetterQueuePanel />)} />
          {FEATURE_FLAGS.RIPPLETRACE_VIEWER && (
            <Route path="/trace" element={platformRoute("Trace", <RippleTraceViewer />)} />
          )}
        </Route>
        <Route path="*" element={<Navigate to="/agent" replace />} />
      </Routes>
    </ErrorBoundary>
  );
}

function PlatformHomeRedirect() {
  const { system } = useSystem();
  const runtimeOnly = system?.runtime?.boot_mode === "runtime-only";
  return <Navigate to={runtimeOnly ? "/flows" : "/agent"} replace />;
}

/**
 * Guards all routes below it. Renders as a layout route (<Outlet />) so React
 * Router can nest child routes without re-mounting the guard on each navigation.
 *
 * Unauthenticated → /login (router-relative, respects basename; no window.location).
 * Authenticated but not admin → terminal NotAdmin view (no navigation, no loop).
 */
function PlatformGuard() {
  const { isAuthenticated, isAdmin } = useAuth();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  if (!isAdmin) {
    return <NotAdmin />;
  }

  return <Outlet />;
}

export default function PlatformApp() {
  return (
    <AuthProvider>
      <BrowserRouter basename="/platform">
        <Routes>
          {/* Public: login lives outside the guard */}
          <Route path="/login" element={<LoginPage />} />

          {/* Protected: everything else goes through PlatformGuard */}
          <Route element={<PlatformGuard />}>
            <Route
              path="/*"
              element={
                <SystemProvider skipBoot>
                  <PlatformRoutes />
                </SystemProvider>
              }
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
