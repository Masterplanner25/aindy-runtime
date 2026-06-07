import React, { useEffect, useState } from "react";

import { getHealthDetails } from "../../api/platform.js";
import { useAuth } from "@aindy/ui-kit";
import { AdminAccessRequired } from "../shared/AdminApiErrorBoundary";

const BLUE = "#6cf";
const GREEN = "#4caf50";
const YELLOW = "#ffb300";
const RED = "#f44336";
const DIM = "#888";

// Human-readable labels for technical field names
const FIELD_LABELS = {
  runner_type:                  "Extension runner",
  assurance_class:              "Isolation strength",
  assurance_class_satisfied:    "Isolation requirement met",
  runtime_trust_status:         "Trust level",
  trust_status:                 "Trust level",
  certification_tier:           "Security certification",
  certification_tier_satisfied: "Certification requirement met",
  current_platform:             "Host platform",
  platform_equivalence:         "Platform compatibility",
  required_assurance_class:     "Required isolation (minimum)",
  verification_method:          "Verification method",
  kernel_observable:            "Runtime inspection enabled",
  assurance_ceiling:            "Maximum achievable isolation",
  trusted_python_present:       "Operator-authorized modules loaded",
  total_trusted_modules:        "Authorized module count",
  owner_classes_present:        "Module owner types",
};

// One-line descriptions shown as tooltips on confusing fields
const FIELD_DESCRIPTIONS = {
  runner_type:                  "Controls whether extensions run in-process or inside an isolated container.",
  assurance_class:              "The level of isolation active. Higher = stronger containment of extensions.",
  assurance_class_satisfied:    "Whether active isolation meets the minimum required for this deployment.",
  runtime_trust_status:         "Whether the runtime considers the current environment safe to execute in.",
  certification_tier:           "The security certification level achieved by the current sandbox configuration.",
  kernel_observable:            "Whether host OS tooling can inspect code running inside the sandbox.",
  assurance_ceiling:            "The strongest isolation this platform can provide, regardless of configuration.",
  trusted_python_present:       "Pre-authorized Python modules are loaded. Expected in enterprise deployments.",
  verification_method:          "How the system validates what code is running inside the extension sandbox.",
};

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
  const key = label.replace(/ /g, "_");
  const displayLabel = FIELD_LABELS[key] ?? label.replace(/_/g, " ");
  const tip = FIELD_DESCRIPTIONS[key];
  return (
    <div
      title={tip}
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "0.75rem 1rem",
        border: "1px solid #333",
        borderRadius: "0.75rem",
        background: "#111",
        cursor: tip ? "help" : undefined,
      }}
    >
      <span style={{ color: "#aaa", textTransform: "capitalize" }}>
        {displayLabel}
        {tip && <span style={{ color: "#555", fontSize: "0.65rem", marginLeft: "0.4rem" }}>ⓘ</span>}
      </span>
      <span style={{ color: color ?? "#ccc" }}>{value == null ? "—" : String(value)}</span>
    </div>
  );
}

function SectionLabel({ children, description }) {
  return (
    <div style={{ margin: "1.25rem 0 0.5rem" }}>
      <p
        style={{
          color: BLUE,
          fontSize: "0.8rem",
          textTransform: "uppercase",
          letterSpacing: "0.07em",
          margin: 0,
        }}
      >
        {children}
      </p>
      {description && (
        <p style={{ color: DIM, fontSize: "0.75rem", margin: "0.2rem 0 0" }}>
          {description}
        </p>
      )}
    </div>
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

  // Overall status banner logic
  const allOk = health?.status === "ok" && degradedDomains.length === 0;
  const bannerColor = allOk ? GREEN : YELLOW;
  const bannerBg = allOk ? "rgba(76,175,80,0.08)" : "rgba(255,179,0,0.08)";
  const bannerBorder = allOk ? "rgba(76,175,80,0.25)" : "rgba(255,179,0,0.25)";

  return (
    <div style={{ padding: "1.5rem" }}>
      <h2 style={{ color: BLUE, marginBottom: "0.25rem" }}>Runtime Health</h2>
      <p style={{ color: DIM, fontSize: "0.8rem", marginBottom: "1rem" }}>
        Build {health?.version || "unknown"}
      </p>

      {/* Plain-English status banner */}
      <div style={{
        padding: "0.85rem 1rem",
        border: `1px solid ${bannerBorder}`,
        borderRadius: "0.75rem",
        background: bannerBg,
        marginBottom: "0.5rem",
      }}>
        <p style={{ margin: 0, fontWeight: 600, color: bannerColor, fontSize: "0.9rem" }}>
          {allOk ? "All systems operational" : `Issues detected${degradedDomains.length ? `: ${degradedDomains.join(", ")}` : ""}`}
        </p>
        <p style={{ margin: "0.25rem 0 0", color: DIM, fontSize: "0.75rem" }}>
          {allOk
            ? "The runtime is healthy and all core services are reachable."
            : "One or more components are degraded. Check the sections below for detail."}
        </p>
      </div>

      <SectionLabel description="Database connectivity, extension registry, event bus, and scheduler availability.">
        Core Services
      </SectionLabel>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        {platformChecks.map(([name, status]) => (
          <Card key={name} label={name} value={status} color={statusColor(status)} />
        ))}
      </div>

      <SectionLabel description="How safely third-party extensions are isolated from the host system. Hover any row for details.">
        Extension Isolation
      </SectionLabel>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        <Card label="runner_type" value={current.runner_type} />
        <Card label="assurance_class" value={current.assurance_class} />
        <Card
          label="assurance_class_satisfied"
          value={reqStatus.assurance_class_satisfied ? "yes" : "no"}
          color={boolColor(reqStatus.assurance_class_satisfied)}
        />
        <Card label="runtime_trust_status" value={current.runtime_trust_status} />
        <Card label="certification_tier" value={current.certification_tier} />
        <Card
          label="certification_tier_satisfied"
          value={reqStatus.certification_tier_satisfied ? "yes" : "no"}
          color={boolColor(reqStatus.certification_tier_satisfied)}
        />
        <Card label="current_platform" value={platformSupport.current_platform} />
        <Card label="platform_equivalence" value={platformSupport.current_equivalence_status} />
        {required.assurance_class && (
          <Card label="required_assurance_class" value={required.assurance_class} color={DIM} />
        )}
      </div>

      <SectionLabel description="Methods used to confirm what code is running inside the extension sandbox.">
        Sandbox Verification
      </SectionLabel>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        <Card label="verification_method" value={verification.verification_method} />
        <Card
          label="kernel_observable"
          value={verification.kernel_observable ? "yes" : "no"}
          color={boolColor(verification.kernel_observable)}
        />
        <Card label="assurance_ceiling" value={verification.assurance_ceiling} />
      </div>

      <SectionLabel description="Indicates whether operator-authorized Python modules are pre-loaded in the runtime.">
        Trusted Modules
      </SectionLabel>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        <Card
          label="trusted_python_present"
          value={trustedPy.present ? "yes" : "no"}
          color={trustedPy.present ? YELLOW : GREEN}
        />
        {trustedPy.present && (
          <>
            <Card label="total_trusted_modules" value={trustedPy.total_count} />
            <Card
              label="owner_classes_present"
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
