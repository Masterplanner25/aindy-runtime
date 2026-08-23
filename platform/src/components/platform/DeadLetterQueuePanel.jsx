import { useCallback, useEffect, useState } from "react";

import { safeMap, useAuth, useToast } from "@aindy/ui-kit";

import {
  deleteDeadLetter,
  drainDeadLetters,
  getDeadLetters,
  getQueueHealth,
  replayDeadLetter,
} from "../../api/operator.js";
import { AdminAccessRequired } from "../shared/AdminApiErrorBoundary";
import { Toast } from "../shared/Toast";
import {
  ActionButton,
  EmptyState,
  ErrorState,
  formatCompactNumber,
  formatDateTime,
  InlineBadge,
  LoadingState,
  MetricCard,
  PageShell,
  SurfaceGrid,
  SurfacePanel,
  surfacePalette,
} from "./SurfacePrimitives";

// FR-21 — the async job queue's dead-letter queue, made actionable: replay a job back
// onto the queue, delete one, or drain the lot. The routes are runtime-owned
// (`/platform/queue/dead-letters*`) and had no operator UI here, so the only one lived in
// an app repo.
//
// ★ Not the same record as `/platform/observability/dead-letter`, which lists dead-lettered
// FLOW RUNS. Two different things share the name; this panel is the queue one, and a job
// here can be replayed because its payload was preserved.
export default function DeadLetterQueuePanel() {
  const { isAdmin } = useAuth();
  if (!isAdmin) return <AdminAccessRequired />;
  return <DeadLetterQueueContent />;
}

function DeadLetterQueueContent() {
  const [health, setHealth] = useState(null);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [confirmDrain, setConfirmDrain] = useState(false);
  const { toast, showToast, clearToast } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      // Health is supplementary: a failure there must not hide the queue contents, which
      // are the reason an operator opened this page.
      const [snapshot, deadLetters] = await Promise.all([
        getQueueHealth().catch(() => null),
        getDeadLetters(100),
      ]);
      setHealth(snapshot);
      setItems(Array.isArray(deadLetters?.items) ? deadLetters.items : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load the dead-letter queue.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleReplay = async (jobId) => {
    setBusyId(jobId);
    try {
      await replayDeadLetter(jobId);
      showToast("Job replayed onto the queue.");
      await load();
    } catch (err) {
      showToast(err?.message || "Replay failed.");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (jobId) => {
    setConfirmDeleteId(null);
    setBusyId(jobId);
    try {
      await deleteDeadLetter(jobId);
      showToast("Dead-letter job deleted.");
      await load();
    } catch (err) {
      showToast(err?.message || "Delete failed.");
    } finally {
      setBusyId(null);
    }
  };

  const handleDrain = async () => {
    setConfirmDrain(false);
    setBusyId("__drain__");
    try {
      const result = await drainDeadLetters();
      const drained = result?.drained ?? 0;
      showToast(`Drained ${drained} job${drained === 1 ? "" : "s"} from the DLQ.`);
      await load();
    } catch (err) {
      showToast(err?.message || "Drain failed.");
    } finally {
      setBusyId(null);
    }
  };

  // `health_snapshot()` publishes these at the top level and inside `metrics`; read the
  // top level and fall back, so a shape change in one place does not blank the tiles.
  const metrics = health?.metrics || {};
  const metric = (key) => health?.[key] ?? metrics[key] ?? 0;
  const draining = busyId === "__drain__";

  const drainAction =
    items.length > 0 ? (
      confirmDrain ? (
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: surfacePalette.muted }}>
            Drain all {items.length}?
          </span>
          <ActionButton tone="danger" onClick={handleDrain} disabled={draining}>
            {draining ? "Draining…" : "Confirm"}
          </ActionButton>
          <ActionButton tone="ghost" onClick={() => setConfirmDrain(false)} disabled={draining}>
            Cancel
          </ActionButton>
        </div>
      ) : (
        <ActionButton tone="danger" onClick={() => setConfirmDrain(true)}>
          Drain DLQ
        </ActionButton>
      )
    ) : null;

  return (
    <PageShell
      eyebrow="Queue"
      title="Dead-Letter Queue"
      description="Async jobs that exhausted their retries. Replay one back onto the queue, delete it, or drain the queue."
      actions={
        <div className="flex items-center gap-2">
          {drainAction}
          <ActionButton tone="ghost" onClick={load} disabled={loading}>
            Refresh
          </ActionButton>
        </div>
      }
    >
      <SurfaceGrid>
        <div className="lg:col-span-12">
          <SurfacePanel
            title="Queue health"
            subtitle="A non-zero DLQ depth with a healthy backend means jobs are failing, not that the queue is broken."
          >
            {health ? (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <MetricCard
                  label="DLQ depth"
                  value={formatCompactNumber(metric("dlq_depth"))}
                  tone={metric("dlq_depth") > 0 ? "warning" : "success"}
                />
                <MetricCard label="Queue depth" value={formatCompactNumber(metric("queue_depth"))} tone="info" />
                <MetricCard label="In flight" value={formatCompactNumber(metric("in_flight_count"))} tone="info" />
                <MetricCard
                  label="Backend"
                  value={String(health.backend_name || health.backend || "—")}
                  hint={health.degraded ? health.reason || "degraded" : "healthy"}
                  tone={health.degraded ? "danger" : "success"}
                />
              </div>
            ) : (
              <EmptyState
                title="Queue health unavailable"
                description="The dead-letter list below is still live; only the health snapshot could not be read."
              />
            )}
          </SurfacePanel>
        </div>

        <div className="lg:col-span-12">
          <SurfacePanel title="Dead-lettered jobs" subtitle={`${items.length} shown`}>
            {loading ? <LoadingState label="Loading dead-letter queue" /> : null}
            {!loading && error ? <ErrorState message={error} onRetry={load} /> : null}
            {!loading && !error && items.length === 0 ? (
              <EmptyState
                title="Dead-letter queue is empty"
                description="Jobs that exhaust their retries appear here, ready to replay or clear."
              />
            ) : null}

            {!loading && !error && items.length > 0 ? (
              <div className="space-y-3">
                {safeMap(items, (item, index) => {
                  const jobId = item?.job_id || item?.idempotency_key || String(index);
                  const retry = item?.retry_metadata || {};
                  const busy = busyId === jobId;
                  return (
                    <div
                      key={jobId}
                      className="flex flex-col gap-3 rounded-[18px] border px-4 py-3 md:flex-row md:items-center md:justify-between"
                      style={{ borderColor: surfacePalette.border }}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <InlineBadge tone="warning">{item?.task_name || "unknown task"}</InlineBadge>
                          <InlineBadge>{jobId}</InlineBadge>
                          {retry.attempt_count != null ? (
                            <InlineBadge tone="danger">
                              {retry.attempt_count}/{retry.max_attempts ?? "?"} attempts
                            </InlineBadge>
                          ) : null}
                        </div>
                        {item?.enqueued_at ? (
                          <div className="mt-1 text-xs" style={{ color: surfacePalette.muted }}>
                            enqueued {formatDateTime(item.enqueued_at)}
                          </div>
                        ) : null}
                      </div>
                      <div className="flex items-center gap-2">
                        <ActionButton tone="primary" onClick={() => handleReplay(jobId)} disabled={busy}>
                          {busy ? "…" : "Replay"}
                        </ActionButton>
                        {confirmDeleteId === jobId ? (
                          <>
                            <ActionButton tone="danger" onClick={() => handleDelete(jobId)} disabled={busy}>
                              Confirm
                            </ActionButton>
                            <ActionButton tone="ghost" onClick={() => setConfirmDeleteId(null)} disabled={busy}>
                              Cancel
                            </ActionButton>
                          </>
                        ) : (
                          <ActionButton tone="ghost" onClick={() => setConfirmDeleteId(jobId)} disabled={busy}>
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
