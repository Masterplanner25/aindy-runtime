import { useCallback, useEffect, useState } from "react";

import { safeMap, useAuth, useToast } from "@aindy/ui-kit";

import { createWebhook, deleteWebhook, getWebhooks } from "../../api/operator.js";
import { AdminAccessRequired } from "../shared/AdminApiErrorBoundary";
import { Toast } from "../shared/Toast";
import {
  ActionButton,
  EmptyState,
  ErrorState,
  formatDateTime,
  InlineBadge,
  LoadingState,
  PageShell,
  SurfaceGrid,
  SurfacePanel,
  surfacePalette,
} from "./SurfacePrimitives";

// FR-21 — outbound event subscriptions. `/platform/webhooks` is a runtime-owned surface
// with full CRUD that this console did not expose, so the only operator UI for it lived
// in an app repo. Adopted here; the app retires theirs.
//
// Delete is confirm-gated in place rather than through a modal: the destructive action
// and the row it destroys must stay visible together, because "which one did I click"
// is the mistake this guards against.
export default function WebhooksPanel() {
  const { isAdmin } = useAuth();
  if (!isAdmin) return <AdminAccessRequired />;
  return <WebhooksContent />;
}

const inputStyle = {
  background: "#0d1117",
  border: `1px solid ${surfacePalette.border}`,
  borderRadius: 8,
  color: surfacePalette.text,
  fontSize: 13,
  padding: "9px 12px",
  outline: "none",
  width: "100%",
  boxSizing: "border-box",
};

function WebhooksContent() {
  const [webhooks, setWebhooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [eventType, setEventType] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [confirmId, setConfirmId] = useState(null);
  const { toast, showToast, clearToast } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await getWebhooks();
      setWebhooks(Array.isArray(result?.webhooks) ? result.webhooks : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load webhooks.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async () => {
    if (!eventType.trim() || !callbackUrl.trim()) {
      showToast("Event type and callback URL are required.");
      return;
    }
    setCreating(true);
    try {
      await createWebhook({
        event_type: eventType.trim(),
        callback_url: callbackUrl.trim(),
        secret: secret.trim() || undefined,
      });
      showToast("Webhook subscription created.");
      setEventType("");
      setCallbackUrl("");
      setSecret("");
      await load();
    } catch (err) {
      showToast(err?.message || "Create failed.");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id) => {
    setConfirmId(null);
    setBusyId(id);
    try {
      await deleteWebhook(id);
      showToast("Webhook subscription deleted.");
      await load();
    } catch (err) {
      showToast(err?.message || "Delete failed.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <PageShell
      eyebrow="Integrations"
      title="Webhooks"
      description="Outbound event subscriptions. Register a callback URL for an event type and the runtime posts to it when that event fires."
      actions={
        <ActionButton tone="ghost" onClick={load} disabled={loading}>
          Refresh
        </ActionButton>
      }
    >
      <SurfaceGrid>
        <div className="lg:col-span-12">
          <SurfacePanel
            title="New subscription"
            subtitle="event_type and callback_url are required; an optional secret signs deliveries (X-AINDY-Signature)."
          >
            <div className="grid gap-3 md:grid-cols-3">
              <input
                style={inputStyle}
                placeholder="event_type (e.g. execution.completed)"
                value={eventType}
                onChange={(event) => setEventType(event.target.value)}
              />
              <input
                style={inputStyle}
                placeholder="callback_url (https://…)"
                value={callbackUrl}
                onChange={(event) => setCallbackUrl(event.target.value)}
              />
              <input
                style={inputStyle}
                placeholder="secret (optional)"
                value={secret}
                onChange={(event) => setSecret(event.target.value)}
              />
            </div>
            <div className="mt-3">
              <ActionButton tone="primary" onClick={handleCreate} disabled={creating}>
                {creating ? "Creating…" : "Create subscription"}
              </ActionButton>
            </div>
          </SurfacePanel>
        </div>

        <div className="lg:col-span-12">
          <SurfacePanel title="Subscriptions" subtitle={`${webhooks.length} active`}>
            {loading ? <LoadingState label="Loading webhooks" /> : null}
            {!loading && error ? <ErrorState message={error} onRetry={load} /> : null}
            {!loading && !error && webhooks.length === 0 ? (
              <EmptyState
                title="No webhook subscriptions"
                description="Create one above to receive outbound events."
              />
            ) : null}

            {!loading && !error && webhooks.length > 0 ? (
              <div className="space-y-3">
                {safeMap(webhooks, (hook, index) => {
                  const id = hook?.id || hook?.subscription_id || String(index);
                  const busy = busyId === id;
                  return (
                    <div
                      key={id}
                      className="flex flex-col gap-3 rounded-[18px] border px-4 py-3 md:flex-row md:items-center md:justify-between"
                      style={{ borderColor: surfacePalette.border }}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <InlineBadge tone="info">{hook?.event_type || "—"}</InlineBadge>
                          {hook?.signed ? <InlineBadge tone="success">signed</InlineBadge> : null}
                          {(hook?.delivery_failures ?? 0) > 0 ? (
                            <InlineBadge tone="danger">{hook.delivery_failures} failed</InlineBadge>
                          ) : null}
                        </div>
                        <div className="mt-1 truncate text-sm" style={{ color: surfacePalette.text }}>
                          {hook?.callback_url}
                        </div>
                        <div className="mt-1 text-xs" style={{ color: surfacePalette.muted }}>
                          {hook?.created_at ? `created ${formatDateTime(hook.created_at)}` : ""}
                          {hook?.delivery_successes != null ? ` · ${hook.delivery_successes} delivered` : ""}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {confirmId === id ? (
                          <>
                            <ActionButton tone="danger" onClick={() => handleDelete(id)} disabled={busy}>
                              {busy ? "Deleting…" : "Confirm"}
                            </ActionButton>
                            <ActionButton tone="ghost" onClick={() => setConfirmId(null)} disabled={busy}>
                              Cancel
                            </ActionButton>
                          </>
                        ) : (
                          <ActionButton tone="ghost" onClick={() => setConfirmId(id)} disabled={busy}>
                            Delete
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
