import React, { useEffect, useState } from "react";

import { getHealthDetails } from "../../api/platform.js";
import { useAuth } from "@aindy/ui-kit";
import { AdminAccessRequired } from "../shared/AdminApiErrorBoundary";

const BLUE = "#6cf";
const GREEN = "#4caf50";
const YELLOW = "#ffb300";
const RED = "#f44336";
const DIM = "#888";

function boolColor(val) {
  return val ? GREEN : RED;
}

function statusColor(val) {
  const v = String(val ?? "").toLowerCase();
  if (["ok", "certified", "satisfied", "true", "yes"].includes(v)) return GREEN;
  if (["degraded", "warning", "partial", "pending"].includes(v)) return YELLOW;
  if (["error", "failed", "unsatisfied", "false", "no", "missing"].includes(v)) return RED;
  return "#ccc";
}

function Card({ label, value, color }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "0.75rem 1rem",
        border: "1px solid #333",
        borderRadius: "0.75rem",
        background: "#111",
      }}
    >
      <span style={{ color: "#aaa", textTransform: "capitalize" }}>
        {label.replace(/_/g, " ")}
      </span>
      <span style={{ color: color ?? "#ccc" }}>{value == null ? "—" : String(value)}</span>
    </div>
  );
}

function SectionLabel({ children }) {
  return (
    <p
      style={{
        color: BLUE,
        fontSize: "0.8rem",
        textTransform: "uppercase",
        letterSpacing: "0.07em",
        margin: "1.25rem 0 0.5rem",
      }}
    >
      {children}
    </p>
  );
}

export default function HealthDashboard() {
  const { isAdmin } = useAuth();
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const payload = await getHealthDetails();
        setHealth(payload);
      } catch {
        setError("Failed to load runtime health");
      } finally {
        setLoading(false);
      }
    };

    fetchHealth();
  }, []);

  if (!isAdmin) return <AdminAccessRequired />;
  if (loading) return <p>Loading health dashboard...</p>;
  if (error) return <p style={{ color: RED }}>{error}</p>;

  const platformChecks = Object.entries(health?.platform || {});
  const degradedDomains = health?.degraded_domains || [];
  const posture = health?.plugin_sandbox_posture || {};
  const current = posture.current || {};
  const required = posture.required || {};
  const reqStatus = posture.requirement_status || {};
  const platformSupport = posture.platform_support || {};
  const verification = health?.sandbox_verification_posture || {};
  const trustedPy = health?.trusted_python_execution || {};
  const conditions = health?.runtime_conditions || [];

  return (
    <div style={{ padding: "1.5rem" }}>
      <h2 style={{ color: BLUE }}>A.I.N.D.Y. Runtime Health</h2>
      <p>Status: {health?.status || "unknown"}</p>
      <p>Build: {health?.version || "unknown"}</p>
      <p>
        Degraded Domains:{" "}
        {degradedDomains.length ? (
          <span style={{ color: RED }}>{degradedDomains.join(", ")}</span>
        ) : (
          <span style={{ color: GREEN }}>none</span>
        )}
      </p>

      <SectionLabel>Platform</SectionLabel>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        {platformChecks.map(([name, status]) => (
          <Card key={name} label={name} value={status} color={statusColor(status)} />
        ))}
      </div>

      <SectionLabel>Sandbox Posture</SectionLabel>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        <Card label="runner type" value={current.runner_type} />
        <Card label="assurance class" value={current.assurance_class} />
        <Card
          label="assurance class satisfied"
          value={reqStatus.assurance_class_satisfied ? "yes" : "no"}
          color={boolColor(reqStatus.assurance_class_satisfied)}
        />
        <Card label="trust status" value={current.runtime_trust_status} />
        <Card label="certification tier" value={current.certification_tier} />
        <Card
          label="certification satisfied"
          value={reqStatus.certification_tier_satisfied ? "yes" : "no"}
          color={boolColor(reqStatus.certification_tier_satisfied)}
        />
        <Card label="current platform" value={platformSupport.current_platform} />
        <Card label="platform equivalence" value={platformSupport.current_equivalence_status} />
        {required.assurance_class && (
          <Card label="required assurance class" value={required.assurance_class} color={DIM} />
        )}
      </div>

      <SectionLabel>Verification</SectionLabel>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        <Card label="verification method" value={verification.verification_method} />
        <Card
          label="kernel observable"
          value={verification.kernel_observable ? "yes" : "no"}
          color={boolColor(verification.kernel_observable)}
        />
        <Card label="assurance ceiling" value={verification.assurance_ceiling} />
      </div>

      <SectionLabel>Trusted Python</SectionLabel>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        <Card
          label="trusted python present"
          value={trustedPy.present ? "yes" : "no"}
          color={trustedPy.present ? YELLOW : GREEN}
        />
        {trustedPy.present && (
          <>
            <Card label="total trusted modules" value={trustedPy.total_count} />
            <Card
              label="owner classes"
              value={(trustedPy.owner_classes_present || []).join(", ") || "none"}
            />
          </>
        )}
      </div>

      {conditions.length > 0 && (
        <>
          <SectionLabel>Runtime Conditions</SectionLabel>
          <div style={{ display: "grid", gap: "0.75rem" }}>
            {conditions.map((c) => (
              <div
                key={c.code}
                style={{
                  padding: "0.75rem 1rem",
                  border: "1px solid #333",
                  borderRadius: "0.75rem",
                  background: "#111",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginBottom: "0.35rem",
                  }}
                >
                  <span style={{ color: "#ccc", fontWeight: "bold" }}>{c.code}</span>
                  <span style={{ color: statusColor(c.classification), fontSize: "0.8rem" }}>
                    {c.classification}
                  </span>
                </div>
                <p style={{ margin: "0 0 0.25rem", color: "#aaa", fontSize: "0.85rem" }}>
                  {c.detail}
                </p>
                <p style={{ margin: 0, color: DIM, fontSize: "0.8rem" }}>
                  {c.component} — {c.production_behavior}
                </p>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
