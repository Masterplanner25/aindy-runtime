import { useCallback, useEffect, useState } from "react";

import { safeMap, useToast } from "@aindy/ui-kit";

import { getMcpGrants, revokeMcpGrant } from "../../api/operator.js";
import { Toast } from "../shared/Toast";
import {
  ActionButton,
  EmptyState,
  ErrorState,
  formatDateTime,
  formatRelativeTime,
  InlineBadge,
  LoadingState,
  PageShell,
  SurfaceGrid,
  SurfacePanel,
  surfacePalette,
} from "./SurfacePrimitives";

// FR-66 — the signed-in user's connected MCP apps: every OAuth grant they gave a client
// (ChatGPT, claude.ai, …) through the MCP endpoint's consent page. `/auth/mcp-grants` is
// per-user and session-only, so this lists YOUR connections, never anyone else's — an
// admin sees their own here like any user.
//
// The redirect host is shown beside the name on purpose: a client's name is whatever it
// registered, while the host is where its codes were sent. Ending a connection is
// confirm-gated in place (the webhook panel's reasoning) and stops every token issued under
// it within seconds; the client has to ask for consent again.
export default function ConnectedAppsPanel() {
  const [grants, setGrants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [confirmId, setConfirmId] = useState(null);
  const { toast, showToast, clearToast } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await getMcpGrants();
      setGrants(Array.isArray(result?.grants) ? result.grants : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load connected apps.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleRevoke = async (id) => {
    setConfirmId(null);
    setBusyId(id);
    try {
      await revokeMcpGrant(id);
      showToast("Connection ended. Its tokens stop working within seconds.");
      await load();
    } catch (err) {
      showToast(err?.message || "Could not end the connection.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <PageShell
      eyebrow="Integrations"
      title="Connected Apps"
      description="Apps you connected to this runtime's MCP server through OAuth. Ending one revokes every token it holds; it must ask for your consent again."
      actions={
        <ActionButton tone="ghost" onClick={load} disabled={loading}>
          Refresh
        </ActionButton>
      }
    >
      <SurfaceGrid>
        <div className="lg:col-span-12">
          <SurfacePanel title="Your connections" subtitle={`${grants.length} active`}>
            {loading ? <LoadingState label="Loading connected apps" /> : null}
            {!loading && error ? <ErrorState message={error} onRetry={load} /> : null}
            {!loading && !error && grants.length === 0 ? (
              <EmptyState
                title="No connected apps"
                description="Add this server's /mcp URL as a connector in ChatGPT or claude.ai; it appears here once you approve it."
              />
            ) : null}

            {!loading && !error && grants.length > 0 ? (
              <div className="space-y-3">
                {safeMap(grants, (grant, index) => {
                  const id = grant?.id || String(index);
                  const busy = busyId === id;
                  return (
                    <div
                      key={id}
                      className="flex flex-col gap-3 rounded-[18px] border px-4 py-3 md:flex-row md:items-center md:justify-between"
                      style={{ borderColor: surfacePalette.border }}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold" style={{ color: surfacePalette.text }}>
                            {grant?.client_name || "(unnamed client)"}
                          </span>
                          {safeMap(grant?.scopes || [], (scope) => (
                            <InlineBadge key={scope} tone={scope.endsWith(".write") ? "warning" : "info"}>
                              {scope}
                            </InlineBadge>
                          ))}
                        </div>
                        <div className="mt-1 truncate text-sm" style={{ color: surfacePalette.muted }}>
                          sends you back to <span style={{ color: surfacePalette.text }}>{grant?.redirect_host || "—"}</span>
                        </div>
                        <div className="mt-1 text-xs" style={{ color: surfacePalette.muted }}>
                          {grant?.created_at ? `connected ${formatDateTime(grant.created_at)}` : ""}
                          {/* last_used_at is written when tokens are issued (code or refresh), not per tool call. */}
                          {grant?.last_used_at ? ` · tokens last issued ${formatRelativeTime(grant.last_used_at)}` : ""}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {confirmId === id ? (
                          <>
                            <ActionButton tone="danger" onClick={() => handleRevoke(id)} disabled={busy}>
                              {busy ? "Ending…" : "Confirm"}
                            </ActionButton>
                            <ActionButton tone="ghost" onClick={() => setConfirmId(null)} disabled={busy}>
                              Cancel
                            </ActionButton>
                          </>
                        ) : (
                          <ActionButton tone="ghost" onClick={() => setConfirmId(id)} disabled={busy}>
                            End connection
                          </ActionButton>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : null}
          </SurfacePanel>
        </div>
      </SurfaceGrid>
      <Toast toast={toast} onDismiss={clearToast} />
    </PageShell>
  );
}
