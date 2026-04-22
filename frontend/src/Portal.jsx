import { useEffect, useState, useCallback } from "react";

const BASE = "/api";

function ts(epoch) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString();
}

function maskKey(key) {
  if (!key || key.length < 12) return key || "—";
  return key.slice(0, 8) + "…" + key.slice(-4);
}

function StatusBadge({ status }) {
  const colors = {
    completed: "#22c55e",
    running: "#3b82f6",
    queued: "#eab308",
    failed: "#ef4444",
  };
  return (
    <span
      className="portal-badge"
      style={{ background: colors[status] || "#64748b" }}
    >
      {status}
    </span>
  );
}

function formatCurrency(amount, currency) {
  if (amount == null) return "—";
  const value = amount / 100;
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: (currency || "brl").toUpperCase(),
  }).format(value);
}

export default function Portal() {
  const [profile, setProfile] = useState(null);
  const [usage, setUsage] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [billing, setBilling] = useState(null);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("overview");

  const apiKey =
    localStorage.getItem("api_key") ||
    import.meta.env.VITE_API_KEY ||
    "demo_ttp_free_2604";

  const headers = {
    "X-API-Key": apiKey,
    "Content-Type": "application/json",
  };

  const load = useCallback(async () => {
    setError(null);
    try {
      const [pRes, uRes, jRes, bRes] = await Promise.all([
        fetch(`${BASE}/portal/profile`, { headers }),
        fetch(`${BASE}/portal/usage`, { headers }),
        fetch(`${BASE}/portal/jobs?limit=20`, { headers }),
        fetch(`${BASE}/portal/billing`, { headers }),
      ]);

      if (!pRes.ok) {
        const d = await pRes.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${pRes.status}`);
      }

      setProfile(await pRes.json());
      setUsage(await uRes.json());
      const jData = await jRes.json();
      setJobs(jData.jobs || []);
      setBilling(await bRes.json());
    } catch (e) {
      setError(e.message);
    }
  }, [apiKey]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [load]);

  if (error) {
    return (
      <div className="portal-page">
        <div className="portal-card">
          <h2>My Account</h2>
          <div className="portal-error">{error}</div>
          <p style={{ color: "#94a3b8", fontSize: "0.85rem", marginTop: "0.5rem" }}>
            Make sure you have a valid API key set. Go to <strong>Get API Key</strong> to sign up.
          </p>
        </div>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="portal-page">
        <div className="portal-card">
          <h2>My Account</h2>
          <p style={{ color: "#94a3b8" }}>Loading…</p>
        </div>
      </div>
    );
  }

  const tierColors = { free: "#94a3b8", pro: "#3b82f6", enterprise: "#a855f7" };

  return (
    <div className="portal-page">
      <div className="portal-card">
        <div className="portal-header">
          <h2>My Account</h2>
          <span
            className="portal-tier-badge"
            style={{ background: tierColors[profile.tier] || "#64748b" }}
          >
            {profile.tier.toUpperCase()}
          </span>
        </div>

        {/* Tabs */}
        <div className="portal-tabs">
          {["overview", "jobs", "billing"].map((t) => (
            <button
              key={t}
              className={tab === t ? "active" : ""}
              onClick={() => setTab(t)}
            >
              {t === "overview" ? "Overview" : t === "jobs" ? "Batch Jobs" : "Billing"}
            </button>
          ))}
        </div>

        {/* Overview */}
        {tab === "overview" && (
          <div className="portal-section">
            <div className="portal-grid">
              <div className="portal-stat">
                <span className="portal-stat-label">API Key</span>
                <span className="portal-stat-value mono">
                  {profile.api_key_masked}
                </span>
              </div>
              <div className="portal-stat">
                <span className="portal-stat-label">Email</span>
                <span className="portal-stat-value">{profile.email || "—"}</span>
              </div>
              <div className="portal-stat">
                <span className="portal-stat-label">Member Since</span>
                <span className="portal-stat-value">{ts(profile.created)}</span>
              </div>
            </div>

            <h3>Usage</h3>
            <div className="portal-grid">
              <div className="portal-stat">
                <span className="portal-stat-label">Requests (session)</span>
                <span className="portal-stat-value">
                  {usage?.requests_total?.toLocaleString() ?? "—"}
                </span>
              </div>
              <div className="portal-stat">
                <span className="portal-stat-label">Current Minute</span>
                <span className="portal-stat-value">
                  {usage?.requests_current_minute ?? 0} / {usage?.rate_limit ?? "—"}
                </span>
              </div>
              <div className="portal-stat">
                <span className="portal-stat-label">Towers Created</span>
                <span className="portal-stat-value">
                  {usage?.towers_created ?? 0} / {usage?.towers_limit ?? "—"}
                </span>
              </div>
            </div>

            <h3>Plan Limits</h3>
            <div className="portal-grid">
              <div className="portal-stat">
                <span className="portal-stat-label">Requests / min</span>
                <span className="portal-stat-value">{profile.limits?.requests_per_min}</span>
              </div>
              <div className="portal-stat">
                <span className="portal-stat-label">Max Towers</span>
                <span className="portal-stat-value">{profile.limits?.max_towers?.toLocaleString()}</span>
              </div>
              <div className="portal-stat">
                <span className="portal-stat-label">Max Batch Rows</span>
                <span className="portal-stat-value">{profile.limits?.max_batch_rows?.toLocaleString()}</span>
              </div>
              <div className="portal-stat">
                <span className="portal-stat-label">PDF Export</span>
                <span className="portal-stat-value">{profile.limits?.pdf_export ? "✓" : "✗"}</span>
              </div>
            </div>
          </div>
        )}

        {/* Batch Jobs */}
        {tab === "jobs" && (
          <div className="portal-section">
            {jobs.length === 0 ? (
              <p className="portal-empty">No batch jobs yet.</p>
            ) : (
              <div className="portal-table-wrap">
                <table className="portal-table">
                  <thead>
                    <tr>
                      <th>Job ID</th>
                      <th>Status</th>
                      <th>Progress</th>
                      <th>Tower</th>
                      <th>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((j) => (
                      <tr key={j.id}>
                        <td className="mono">{j.id.slice(0, 8)}…</td>
                        <td><StatusBadge status={j.status} /></td>
                        <td>{j.progress}/{j.total}</td>
                        <td>{j.tower_id}</td>
                        <td>{ts(j.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Billing */}
        {tab === "billing" && (
          <div className="portal-section">
            <div className="portal-grid">
              <div className="portal-stat">
                <span className="portal-stat-label">Current Plan</span>
                <span className="portal-stat-value" style={{ color: tierColors[profile.tier] }}>
                  {profile.tier.charAt(0).toUpperCase() + profile.tier.slice(1)}
                </span>
              </div>
              <div className="portal-stat">
                <span className="portal-stat-label">Subscription</span>
                <span className="portal-stat-value">
                  {billing?.has_subscription ? "Active" : "None"}
                </span>
              </div>
            </div>

            {billing?.invoices?.length > 0 ? (
              <>
                <h3>Recent Invoices</h3>
                <div className="portal-table-wrap">
                  <table className="portal-table">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Amount</th>
                        <th>Status</th>
                        <th>Invoice</th>
                      </tr>
                    </thead>
                    <tbody>
                      {billing.invoices.map((inv) => (
                        <tr key={inv.id}>
                          <td>{ts(inv.created)}</td>
                          <td>{formatCurrency(inv.amount_paid || inv.amount_due, inv.currency)}</td>
                          <td><StatusBadge status={inv.status} /></td>
                          <td>
                            {inv.invoice_url ? (
                              <a
                                href={inv.invoice_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="portal-link"
                              >
                                View
                              </a>
                            ) : (
                              "—"
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <p className="portal-empty" style={{ marginTop: "1rem" }}>
                {profile.tier === "free"
                  ? "Upgrade to Pro or Enterprise to see billing history."
                  : "No invoices found."}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
