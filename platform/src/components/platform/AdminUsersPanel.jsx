import React, { useState, useEffect, useCallback } from "react";
import { useAuth } from "@aindy/ui-kit";
import { AdminAccessRequired } from "../shared/AdminApiErrorBoundary";
import { LoadingPanel } from "../shared/LoadingPanel";
import { listUsers, promoteUser } from "../../api/admin.js";

const C = {
  bg0: "#0d1117",
  bg1: "#161b22",
  border0: "#21262d",
  border1: "#30363d",
  text0: "#c9d1d9",
  text1: "#8b949e",
  accent: "#6cf",
};

function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export default function AdminUsersPanel() {
  const { isAdmin, user: currentUser } = useAuth();
  if (!isAdmin) return <AdminAccessRequired />;

  const [users, setUsers] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [promoting, setPromoting] = useState(null);
  const [toast, setToast] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listUsers()
      .then((d) => setUsers(d?.users ?? []))
      .catch((e) => setError(e?.message || "Failed to load users."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const handlePromote = async (userId, email) => {
    setPromoting(userId);
    try {
      await promoteUser(userId);
      setToast(`${email} promoted to admin.`);
      setTimeout(() => setToast(null), 3500);
      load();
    } catch (e) {
      setError(e?.message || "Promotion failed.");
    } finally {
      setPromoting(null);
    }
  };

  return (
    <div style={{ padding: 20, color: C.text0, fontFamily: "sans-serif", maxWidth: 900 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div>
          <h2 style={{ margin: 0, color: "#fff" }}>Users</h2>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: C.text1 }}>
            Registered accounts. Admin promotion is grant-only — privileges are never revoked here.
          </p>
        </div>
        <button
          onClick={load}
          style={{ padding: "6px 14px", background: "none", border: `1px solid ${C.border1}`, borderRadius: 6, color: C.text1, fontSize: 12, cursor: "pointer" }}>
          Refresh
        </button>
      </div>

      {toast && (
        <div style={{ marginBottom: 12, padding: "8px 14px", background: "#0d2a1a", border: "1px solid #1a5c33", borderRadius: 6, fontSize: 12, color: "#4ade80" }}>
          {toast}
        </div>
      )}

      {error && (
        <div style={{ marginBottom: 12, padding: "8px 14px", background: "#1a0d0d", border: "1px solid #5c1a1a", borderRadius: 6, fontSize: 12, color: "#f87171" }}>
          {error}
        </div>
      )}

      {loading && <LoadingPanel label="Loading users..." />}

      {!loading && users && (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border0}` }}>
              {["Email", "Joined", "Status", ""].map((h) => (
                <th key={h} style={{ textAlign: "left", padding: "8px 12px", color: C.text1, fontWeight: 600, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const isYou = u.email === currentUser?.email;
              return (
                <tr key={u.id} style={{ borderBottom: `1px solid ${C.border0}` }}>
                  <td style={{ padding: "10px 12px", color: C.text0 }}>
                    {u.email}
                    {isYou && (
                      <span style={{ marginLeft: 8, fontSize: 10, color: C.text1, border: `1px solid ${C.border1}`, borderRadius: 4, padding: "1px 5px" }}>
                        you
                      </span>
                    )}
                  </td>
                  <td style={{ padding: "10px 12px", color: C.text1, fontSize: 12 }}>
                    {formatDate(u.created_at)}
                  </td>
                  <td style={{ padding: "10px 12px" }}>
                    {u.is_admin ? (
                      <span style={{ fontSize: 11, color: C.accent, border: `1px solid ${C.accent}33`, borderRadius: 4, padding: "2px 7px" }}>
                        admin
                      </span>
                    ) : (
                      <span style={{ fontSize: 11, color: C.text1, border: `1px solid ${C.border1}`, borderRadius: 4, padding: "2px 7px" }}>
                        {u.is_active ? "active" : "inactive"}
                      </span>
                    )}
                  </td>
                  <td style={{ padding: "10px 12px", textAlign: "right" }}>
                    {!u.is_admin && !isYou && (
                      <button
                        disabled={promoting === u.id}
                        onClick={() => handlePromote(u.id, u.email)}
                        style={{
                          padding: "4px 12px",
                          background: "none",
                          border: `1px solid ${C.accent}66`,
                          borderRadius: 5,
                          color: C.accent,
                          fontSize: 11,
                          cursor: promoting === u.id ? "not-allowed" : "pointer",
                          opacity: promoting === u.id ? 0.5 : 1,
                        }}>
                        {promoting === u.id ? "Promoting…" : "Promote to admin"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {!loading && users?.length === 0 && (
        <p style={{ color: C.text1, fontSize: 13 }}>No registered users found.</p>
      )}
    </div>
  );
}
