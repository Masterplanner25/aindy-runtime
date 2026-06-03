import { lazy, type ReactNode } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";

import ErrorBoundary, { RouteErrorBoundary } from "./components/shared/ErrorBoundary";
import { AuthProvider, useAuth } from "@aindy/ui-kit";
import { SystemProvider, useSystem } from "@aindy/ui-kit";

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

function platformRoute(name: string, element: ReactNode) {
  return (
    <RouteErrorBoundary name={name} layer="platform" domain={name}>
      {element}
    </RouteErrorBoundary>
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
                  <ErrorBoundary layer="platform">
                    <Routes>
                      {/* Bare redirects render outside the shell (no chrome on a <Navigate>) */}
                      <Route path="/" element={<PlatformHomeRedirect />} />

                      {/* All real screens render inside the collapsible-sidebar shell */}
                      <Route element={<PlatformShell />}>
                        <Route path="/agent" element={platformRoute("Agent Console", <AgentConsole />)} />
                        <Route path="/flows" element={platformRoute("Flow Engine", <FlowEngineConsole />)} />
                        <Route path="/observability" element={platformRoute("Observability", <ObservabilityDashboard />)} />
                        <Route path="/health" element={platformRoute("Health", <HealthDashboard />)} />
                        <Route path="/executions" element={platformRoute("Executions", <ExecutionConsole />)} />
                        <Route path="/approvals" element={platformRoute("Approvals", <AgentApprovalInbox />)} />
                        <Route path="/registry" element={platformRoute("Registry", <AgentRegistry />)} />
                        <Route path="/trace" element={platformRoute("Trace", <RippleTraceViewer />)} />
                      </Route>

                      <Route path="*" element={<Navigate to="/agent" replace />} />
                    </Routes>
                  </ErrorBoundary>
                </SystemProvider>
              }
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
