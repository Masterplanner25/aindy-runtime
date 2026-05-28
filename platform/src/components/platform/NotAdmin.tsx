import React from "react";
import { useAuth } from "@aindy/ui-kit";

export default function NotAdmin() {
  const { logout, user } = useAuth();

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0a0a0a",
        fontFamily: "monospace",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 420,
          padding: "2rem",
          border: "1px solid #3a1a1a",
          borderRadius: "1rem",
          background: "#111",
          textAlign: "center",
        }}
      >
        <div style={{ color: "#f44", fontSize: "1.5rem", marginBottom: "0.75rem" }}>
          ACCESS DENIED
        </div>
        <div style={{ color: "#777", fontSize: "0.85rem", marginBottom: "0.5rem" }}>
          Admin privileges required for the A.I.N.D.Y. Platform.
        </div>
        {user?.email && (
          <div style={{ color: "#555", fontSize: "0.75rem", marginBottom: "1.5rem" }}>
            Signed in as {user.email}
          </div>
        )}
        <div style={{ color: "#555", fontSize: "0.75rem", marginBottom: "1.5rem" }}>
          Contact your administrator or run:
          <br />
          <code style={{ color: "#aaa" }}>
            aindy-runtime auth promote-admin {user?.email ?? "<email>"}
          </code>
        </div>
        <button
          onClick={logout}
          style={{
            padding: "0.55rem 1.25rem",
            background: "transparent",
            border: "1px solid #444",
            borderRadius: "0.5rem",
            color: "#888",
            fontSize: "0.8rem",
            cursor: "pointer",
          }}
        >
          SIGN OUT
        </button>
      </div>
    </div>
  );
}
