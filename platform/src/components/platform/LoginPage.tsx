import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@aindy/ui-kit";

export default function LoginPage() {
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Already authenticated — go home.
  React.useEffect(() => {
    if (isAuthenticated) {
      navigate("/", { replace: true });
    }
  }, [isAuthenticated, navigate]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "Login failed. Check your credentials.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

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
          maxWidth: 380,
          padding: "2rem",
          border: "1px solid #2a2a2a",
          borderRadius: "1rem",
          background: "#111",
        }}
      >
        <div style={{ marginBottom: "1.75rem", textAlign: "center" }}>
          <div style={{ color: "#00ffaa", fontSize: "1.1rem", letterSpacing: "0.15em" }}>
            A.I.N.D.Y.
          </div>
          <div style={{ color: "#555", fontSize: "0.75rem", marginTop: "0.25rem" }}>
            PLATFORM
          </div>
        </div>

        <form onSubmit={handleSubmit} noValidate>
          <div style={{ marginBottom: "1rem" }}>
            <label
              htmlFor="email"
              style={{ display: "block", color: "#777", fontSize: "0.75rem", marginBottom: "0.35rem" }}
            >
              EMAIL
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              style={{
                width: "100%",
                padding: "0.6rem 0.75rem",
                background: "#0d0d0d",
                border: "1px solid #2a2a2a",
                borderRadius: "0.5rem",
                color: "#e0e0e0",
                fontSize: "0.9rem",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
          </div>

          <div style={{ marginBottom: "1.25rem" }}>
            <label
              htmlFor="password"
              style={{ display: "block", color: "#777", fontSize: "0.75rem", marginBottom: "0.35rem" }}
            >
              PASSWORD
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              style={{
                width: "100%",
                padding: "0.6rem 0.75rem",
                background: "#0d0d0d",
                border: "1px solid #2a2a2a",
                borderRadius: "0.5rem",
                color: "#e0e0e0",
                fontSize: "0.9rem",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
          </div>

          {error && (
            <div
              role="alert"
              style={{
                marginBottom: "1rem",
                padding: "0.6rem 0.75rem",
                background: "#1a0a0a",
                border: "1px solid #5a1a1a",
                borderRadius: "0.5rem",
                color: "#f88",
                fontSize: "0.8rem",
              }}
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: "100%",
              padding: "0.65rem",
              background: loading ? "#1a3a2a" : "#00ffaa22",
              border: "1px solid #00ffaa55",
              borderRadius: "0.5rem",
              color: loading ? "#555" : "#00ffaa",
              fontSize: "0.85rem",
              letterSpacing: "0.08em",
              cursor: loading ? "not-allowed" : "pointer",
            }}
          >
            {loading ? "SIGNING IN…" : "SIGN IN"}
          </button>
        </form>
      </div>
    </div>
  );
}
