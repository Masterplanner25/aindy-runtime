import React, { useCallback, useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useAuth } from "@aindy/ui-kit";
import { safeMap } from "@aindy/ui-kit";
import { getSystemState } from "../../api/operator.js";
import { AdminAccessRequired } from "../shared/AdminApiErrorBoundary";
import {
  ActionButton,
  EmptyState,
  ErrorState,
  formatCompactNumber,
  InlineBadge,
  LoadingState,
  MetricCard,
  PageShell,
  statusTone,
  SurfaceGrid,
  SurfacePanel,
  surfacePalette,
} from "./SurfacePrimitives";

function domainTone(status) {
  return status === "healthy" ? "success" : status === "degraded" ? "warning" : "neutral";
}

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  return (
    <div
      className="rounded-2xl border px-4 py-3 shadow-2xl"
      style={{ background: "rgba(10,12,18,0.98)", borderColor: surfacePalette.border }}>
      <div className="space-y-1 text-sm">
        {safeMap(payload, (item) => (
          <div key={item.dataKey} className="flex items-center justify-between gap-4">
            <span style={{ color: item.color }}>{item.name}</span>
            <span style={{ color: surfacePalette.text }}>{item.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ExecutionConsole() {
  const { isAdmin } = useAuth();
  if (!isAdmin) return <AdminAccessRequired />;

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await getSystemState());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load system state.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const apps = data?.connected_apps ?? [];
  const domains = data?.domain_health ?? [];
  const registry = data?.registry ?? {};
  const exec = data?.execution_summary ?? {};
  const flows = exec.flow_runs ?? {};
  const agents = exec.agent_runs ?? {};
  const eu24 = exec.execution_units_24h ?? {};

  const degradedCount = domains.filter((d) => d.status === "degraded").length;
  const healthyCount = domains.filter((d) => d.status === "healthy").length;

  const flowSeries = safeMap(
    Object.entries(flows.by_status ?? {}),
    ([status, count]) => ({ status, count })
  );
  const agentSeries = safeMap(
    Object.entries(agents.by_status ?? {}),
    ([status, count]) => ({ status, count })
  );

  return (
    <PageShell
      eyebrow="Runtime Observability"
      title="Execution Console"
      description="Live view of connected apps, domain health, and execution pipeline state — everything the runtime knows about itself and what's plugged into it."
      actions={<ActionButton tone="ghost" onClick={load}>Refresh</ActionButton>}>

      <SurfaceGrid>
        <div className="lg:col-span-3">
          <MetricCard
            label="Connected Apps"
            value={formatCompactNumber(apps.length)}
            hint="First-party apps registered with the runtime"
            tone="info" />
        </div>
        <div className="lg:col-span-3">
          <MetricCard
            label="Domain Health"
            value={degradedCount > 0 ? `${degradedCount} degraded` : `${healthyCount} healthy`}
            hint={`${domains.length} domains tracked`}
            tone={degradedCount > 0 ? "warning" : "success"} />
        </div>
        <div className="lg:col-span-3">
          <MetricCard
            label="Flow Runs"
            value={formatCompactNumber(flows.total ?? 0)}
            hint={`${formatCompactNumber(flows.by_status?.running ?? 0)} active`}
            tone="info" />
        </div>
        <div className="lg:col-span-3">
          <MetricCard
            label="Error Rate (24h)"
            value={`${eu24.error_rate_pct ?? 0}%`}
            hint={`${formatCompactNumber(eu24.total ?? 0)} pipeline executions`}
            tone={(eu24.error_rate_pct ?? 0) > 0 ? "warning" : "success"} />
        </div>
      </SurfaceGrid>

      {loading ? <LoadingState label="Loading system state" /> : null}
      {!loading && error ? <ErrorState message={error} onRetry={load} /> : null}

      {!loading && !error ? (
        <>
          <SurfaceGrid>
            <div className="lg:col-span-7">
              <SurfacePanel
                title="Connected Apps"
                subtitle="First-party applications registered with this runtime instance.">
                {apps.length ? (
                  <div className="space-y-3">
                    {safeMap(apps, (app) => (
                      <div
                        key={app.name}
                        className="rounded-[18px] border px-4 py-3"
                        style={{ borderColor: surfacePalette.border }}>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold" style={{ color: surfacePalette.text }}>
                            {app.name}
                          </span>
                          {app.trust_class ? (
                            <InlineBadge tone="info">{app.trust_class}</InlineBadge>
                          ) : null}
                          {app.execution_model ? (
                            <InlineBadge>{app.execution_model}</InlineBadge>
                          ) : null}
                          {app.has_health_check ? (
                            <InlineBadge tone="success">health check</InlineBadge>
                          ) : null}
                        </div>
                        {app.module_name ? (
                          <div className="mt-1 text-xs" style={{ color: surfacePalette.muted }}>
                            {app.module_name}
                          </div>
                        ) : null}
                        {app.dependencies?.length ? (
                          <div className="mt-1 text-xs" style={{ color: surfacePalette.muted }}>
                            deps: {app.dependencies.join(", ")}
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    title="No apps registered"
                    description="Apps appear here after calling publish_bootstrap_registration during bootstrap." />
                )}
              </SurfacePanel>
            </div>

            <div className="lg:col-span-5">
              <SurfacePanel
                title="Domain Health"
                subtitle="Core domains published by connected apps — healthy vs. degraded.">
                {domains.length ? (
                  <div className="flex flex-wrap gap-2">
                    {safeMap(domains, (d) => (
                      <InlineBadge key={d.domain} tone={domainTone(d.status)}>
                        {d.domain}
                      </InlineBadge>
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    title="No domains tracked"
                    description="Domains appear here after apps call publish_core_domains." />
                )}
              </SurfacePanel>
            </div>
          </SurfaceGrid>

          <SurfaceGrid>
            <div className="lg:col-span-6">
              <SurfacePanel
                title="Flow Run State"
                subtitle="All-time flow run counts by status.">
                {flowSeries.length ? (
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={flowSeries}>
                        <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                        <XAxis dataKey="status" stroke={surfacePalette.muted} />
                        <YAxis allowDecimals={false} stroke={surfacePalette.muted} />
                        <Tooltip content={<ChartTooltip />} />
                        <Bar dataKey="count" name="Runs" fill={surfacePalette.accent} radius={[8, 8, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="No flow runs" description="Flow execution counts appear after workflow activity." />
                )}
              </SurfacePanel>
            </div>

            <div className="lg:col-span-6">
              <SurfacePanel
                title="Agent Run State"
                subtitle="All-time agent run counts by status.">
                {agentSeries.length ? (
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={agentSeries}>
                        <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                        <XAxis dataKey="status" stroke={surfacePalette.muted} />
                        <YAxis allowDecimals={false} stroke={surfacePalette.muted} />
                        <Tooltip content={<ChartTooltip />} />
                        <Bar dataKey="count" name="Runs" fill={surfacePalette.info} radius={[8, 8, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="No agent runs" description="Agent run counts appear after agent activity." />
                )}
              </SurfacePanel>
            </div>
          </SurfaceGrid>

          <SurfaceGrid>
            <div className="lg:col-span-4">
              <SurfacePanel
                title="Execution Pipeline (24h)"
                subtitle="Execution unit throughput and error rate over the last 24 hours.">
                <div className="space-y-4">
                  {[
                    { label: "Total Executions", value: formatCompactNumber(eu24.total ?? 0), tone: "info" },
                    { label: "Completed", value: formatCompactNumber(eu24.completed ?? 0), tone: "success" },
                    { label: "Failed", value: formatCompactNumber(eu24.failed ?? 0), tone: eu24.failed > 0 ? "danger" : "neutral" },
                    { label: "Avg Wall Time", value: `${eu24.avg_wall_time_ms ?? 0} ms`, tone: "neutral" },
                    { label: "Error Rate", value: `${eu24.error_rate_pct ?? 0}%`, tone: (eu24.error_rate_pct ?? 0) > 0 ? "warning" : "success" },
                  ].map(({ label, value, tone }) => (
                    <div key={label} className="flex items-center justify-between">
                      <span className="text-sm" style={{ color: surfacePalette.muted }}>{label}</span>
                      <InlineBadge tone={tone}>{value}</InlineBadge>
                    </div>
                  ))}
                </div>
              </SurfacePanel>
            </div>

            <div className="lg:col-span-4">
              <SurfacePanel
                title="Registry"
                subtitle="Runtime-registered capabilities — syscalls, tools, extensions, jobs.">
                <div className="space-y-4">
                  {[
                    { label: "Syscalls", value: formatCompactNumber(registry.syscall_count ?? 0), tone: "info" },
                    { label: "Agent Tools", value: formatCompactNumber(registry.tool_count ?? 0), tone: "info" },
                    { label: "Extensions", value: formatCompactNumber(registry.extension_count ?? 0), tone: "neutral" },
                    { label: "Scheduled Jobs", value: formatCompactNumber(registry.scheduled_job_count ?? 0), tone: "neutral" },
                    { label: "Event Types", value: formatCompactNumber(registry.event_type_count ?? 0), tone: "neutral" },
                  ].map(({ label, value, tone }) => (
                    <div key={label} className="flex items-center justify-between">
                      <span className="text-sm" style={{ color: surfacePalette.muted }}>{label}</span>
                      <InlineBadge tone={tone}>{value}</InlineBadge>
                    </div>
                  ))}
                </div>
              </SurfacePanel>
            </div>

            <div className="lg:col-span-4">
              <SurfacePanel
                title="Event Types"
                subtitle="Event types published by connected apps and the runtime.">
                {registry.event_types?.length ? (
                  <div className="flex flex-wrap gap-2">
                    {safeMap(registry.event_types, (et) => (
                      <InlineBadge key={et}>{et}</InlineBadge>
                    ))}
                  </div>
                ) : (
                  <EmptyState title="No event types" description="Event types appear after apps register them." />
                )}
              </SurfacePanel>
            </div>
          </SurfaceGrid>
        </>
      ) : null}
    </PageShell>
  );
}
