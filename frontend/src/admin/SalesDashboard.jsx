import { useEffect, useState, useCallback } from "react";

/**
 * Admin sales dashboard. Shows:
 *  - Aggregate totals (tenants, MRR, ARR, SSO/white-label adoption)
 *  - Tenants by tier (with MRR contribution)
 *  - Recent signups (last 30 days)
 *  - Top active tenants (last 30 days, by audit-log volume)
 *
 * Auth: requires an admin API key (env ADMIN_API_KEYS on the backend).
 * The key is held in localStorage under "ttp_admin_api_key" — never sent
 * via URL, never exposed to other React routes.
 *
 * Route: /admin/sales (gated by App.jsx).
 */

function fmtBRL(n) {
  if (n == null) return "—";
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    maximumFractionDigits: 0,
  }).format(n);
}

function fmtTs(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("pt-BR");
}

const TIER_COLOR = {
  free: "#94a3b8",
  starter: "#60a5fa",
  pro: "#a855f7",
  business: "#f59e0b",
  enterprise: "#10b981",
  ultra: "#ec4899",
};

export default function SalesDashboard() {
  const [adminKey, setAdminKey] = useState(
    () => localStorage.getItem("ttp_admin_api_key") || ""
  );
  const [keyInput, setKeyInput] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedTenant, setSelectedTenant] = useState(null);

  const load = useCallback(async () => {
    if (!adminKey) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/admin/sales/overview", {
        headers: { "X-API-Key": adminKey },
      });
      if (res.status === 401 || res.status === 403) {
        setError("Admin key inválida ou sem permissão.");
        setData(null);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [adminKey]);

  useEffect(() => {
    if (adminKey) load();
  }, [adminKey, load]);

  const loadTenant = useCallback(
    async (prefix) => {
      try {
        const res = await fetch(`/admin/sales/tenants/${prefix}`, {
          headers: { "X-API-Key": adminKey },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        setSelectedTenant(await res.json());
      } catch (e) {
        setError(String(e));
      }
    },
    [adminKey]
  );

  const exportCSV = useCallback(() => {
    if (!data) return;
    const rows = [
      ["api_key_prefix", "tier", "owner", "email", "billing_cycle", "created_at", "sso"],
      ...data.recent_signups.map((r) => [
        r.api_key_prefix,
        r.tier,
        r.owner,
        r.email,
        r.billing_cycle,
        new Date((r.created_at || 0) * 1000).toISOString(),
        r.sso_enabled ? "yes" : "no",
      ]),
    ];
    const csv = rows.map((r) => r.map((c) => `"${(c ?? "").toString().replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ttp-recent-signups-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [data]);

  // Login screen
  if (!adminKey) {
    return (
      <div style={styles.loginWrap}>
        <div style={styles.loginCard}>
          <h2 style={{ margin: "0 0 12px 0" }}>Admin / Sales Dashboard</h2>
          <p style={{ color: "#64748b", fontSize: 14 }}>
            Cole sua admin API key (definida em <code>ADMIN_API_KEYS</code> no backend).
          </p>
          <input
            type="password"
            placeholder="ttp_admin_..."
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            style={styles.input}
            onKeyDown={(e) => {
              if (e.key === "Enter" && keyInput.trim()) {
                localStorage.setItem("ttp_admin_api_key", keyInput.trim());
                setAdminKey(keyInput.trim());
              }
            }}
          />
          <button
            style={styles.btn}
            disabled={!keyInput.trim()}
            onClick={() => {
              localStorage.setItem("ttp_admin_api_key", keyInput.trim());
              setAdminKey(keyInput.trim());
            }}
          >
            Entrar
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>Sales Dashboard</h1>
          <span style={{ color: "#64748b", fontSize: 13 }}>
            {data?.generated_at ? `Gerado em ${fmtTs(data.generated_at)}` : ""}
          </span>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button style={styles.btnSecondary} onClick={load} disabled={loading}>
            {loading ? "Atualizando..." : "Atualizar"}
          </button>
          <button style={styles.btnSecondary} onClick={exportCSV} disabled={!data}>
            Exportar CSV
          </button>
          <button
            style={styles.btnSecondary}
            onClick={() => {
              localStorage.removeItem("ttp_admin_api_key");
              setAdminKey("");
              setData(null);
            }}
          >
            Sair
          </button>
        </div>
      </header>

      {error && <div style={styles.error}>{error}</div>}

      {data && (
        <>
          {/* Totals */}
          <section style={styles.kpiGrid}>
            <Kpi label="Tenants ativos" value={data.totals.tenants} />
            <Kpi label="MRR" value={fmtBRL(data.totals.mrr_brl)} highlight />
            <Kpi label="ARR" value={fmtBRL(data.totals.arr_brl)} />
            <Kpi label="SSO ativado" value={data.totals.sso_enabled} />
            <Kpi label="White-label" value={data.totals.white_label_enabled} />
          </section>

          {/* By tier */}
          <section style={styles.card}>
            <h2 style={styles.h2}>Tenants por tier</h2>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th>Tier</th>
                  <th style={{ textAlign: "right" }}>Qtd</th>
                  <th style={{ textAlign: "right" }}>MRR</th>
                  <th style={{ textAlign: "right" }}>% MRR</th>
                </tr>
              </thead>
              <tbody>
                {data.by_tier.map((row) => (
                  <tr key={row.tier}>
                    <td>
                      <span style={{ ...styles.tierPill, background: TIER_COLOR[row.tier] || "#94a3b8" }}>
                        {row.tier}
                      </span>
                    </td>
                    <td style={{ textAlign: "right" }}>{row.count}</td>
                    <td style={{ textAlign: "right" }}>{fmtBRL(row.mrr_brl)}</td>
                    <td style={{ textAlign: "right" }}>
                      {data.totals.mrr_brl
                        ? `${((row.mrr_brl / data.totals.mrr_brl) * 100).toFixed(1)}%`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {/* Recent signups */}
          <section style={styles.card}>
            <h2 style={styles.h2}>Signups recentes (30 dias)</h2>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Tier</th>
                  <th>Owner</th>
                  <th>Email</th>
                  <th>Ciclo</th>
                  <th>SSO</th>
                  <th>Criado em</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_signups.map((r) => (
                  <tr
                    key={r.api_key_prefix}
                    style={{ cursor: "pointer" }}
                    onClick={() => loadTenant(r.api_key_prefix)}
                  >
                    <td><code>{r.api_key_prefix}</code></td>
                    <td>
                      <span style={{ ...styles.tierPill, background: TIER_COLOR[r.tier] || "#94a3b8" }}>
                        {r.tier}
                      </span>
                    </td>
                    <td>{r.owner || "—"}</td>
                    <td>{r.email || "—"}</td>
                    <td>{r.billing_cycle || "—"}</td>
                    <td>{r.sso_enabled ? "✓" : ""}</td>
                    <td>{fmtTs(r.created_at)}</td>
                  </tr>
                ))}
                {data.recent_signups.length === 0 && (
                  <tr><td colSpan={7} style={{ color: "#94a3b8" }}>nenhum signup nos últimos 30 dias</td></tr>
                )}
              </tbody>
            </table>
          </section>

          {/* Top active */}
          <section style={styles.card}>
            <h2 style={styles.h2}>Tenants mais ativos (30 dias)</h2>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Tier</th>
                  <th>Owner</th>
                  <th>Email</th>
                  <th style={{ textAlign: "right" }}>Eventos auditoria</th>
                </tr>
              </thead>
              <tbody>
                {data.top_active.map((r) => (
                  <tr
                    key={r.api_key_prefix}
                    style={{ cursor: "pointer" }}
                    onClick={() => loadTenant(r.api_key_prefix)}
                  >
                    <td><code>{r.api_key_prefix}</code></td>
                    <td>
                      <span style={{ ...styles.tierPill, background: TIER_COLOR[r.tier] || "#94a3b8" }}>
                        {r.tier || "?"}
                      </span>
                    </td>
                    <td>{r.owner || "—"}</td>
                    <td>{r.email || "—"}</td>
                    <td style={{ textAlign: "right" }}>{r.events_30d}</td>
                  </tr>
                ))}
                {data.top_active.length === 0 && (
                  <tr><td colSpan={5} style={{ color: "#94a3b8" }}>nenhuma atividade registrada</td></tr>
                )}
              </tbody>
            </table>
          </section>
        </>
      )}

      {selectedTenant && (
        <div style={styles.modalBg} onClick={() => setSelectedTenant(null)}>
          <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>
              Tenant <code>{selectedTenant.api_key_prefix}</code>
            </h3>
            <dl style={styles.dl}>
              <dt>Tier</dt><dd>{selectedTenant.tier}</dd>
              <dt>Owner</dt><dd>{selectedTenant.owner || "—"}</dd>
              <dt>Email</dt><dd>{selectedTenant.email || "—"}</dd>
              <dt>Stripe customer</dt><dd><code>{selectedTenant.stripe_customer_id || "—"}</code></dd>
              <dt>Stripe subscription</dt><dd><code>{selectedTenant.stripe_subscription_id || "—"}</code></dd>
              <dt>Billing cycle</dt><dd>{selectedTenant.billing_cycle || "—"}</dd>
              <dt>Criado em</dt><dd>{fmtTs(selectedTenant.created_at)}</dd>
              <dt>SSO</dt><dd>{selectedTenant.sso_enabled ? `✓ (${selectedTenant.oauth_provider})` : "—"}</dd>
              <dt>White-label</dt><dd>{selectedTenant.white_label_enabled ? "✓" : "—"}</dd>
            </dl>
            <h4>Audit log (últimos 100)</h4>
            <div style={{ maxHeight: 300, overflowY: "auto", border: "1px solid #e2e8f0", padding: 8, fontSize: 12 }}>
              {(selectedTenant.recent_audit || []).map((row, i) => (
                <div key={i} style={{ borderBottom: "1px solid #f1f5f9", padding: "4px 0" }}>
                  <span style={{ color: "#64748b" }}>{fmtTs(row.ts)}</span>{" — "}
                  <strong>{row.action}</strong>
                  {row.target ? <span> · {row.target}</span> : null}
                </div>
              ))}
              {(!selectedTenant.recent_audit || selectedTenant.recent_audit.length === 0) && (
                <span style={{ color: "#94a3b8" }}>sem eventos</span>
              )}
            </div>
            <button style={{ ...styles.btnSecondary, marginTop: 12 }} onClick={() => setSelectedTenant(null)}>
              Fechar
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Kpi({ label, value, highlight }) {
  return (
    <div style={{ ...styles.kpi, ...(highlight ? styles.kpiHighlight : {}) }}>
      <div style={styles.kpiLabel}>{label}</div>
      <div style={styles.kpiValue}>{value}</div>
    </div>
  );
}

const styles = {
  page: { padding: "24px", maxWidth: 1280, margin: "0 auto", fontFamily: "system-ui, -apple-system, sans-serif" },
  header: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 },
  loginWrap: { minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#f8fafc" },
  loginCard: { background: "white", padding: 32, borderRadius: 12, width: 400, boxShadow: "0 4px 24px rgba(0,0,0,0.08)" },
  input: { width: "100%", padding: 12, border: "1px solid #cbd5e1", borderRadius: 8, marginTop: 12, fontSize: 14, boxSizing: "border-box" },
  btn: { width: "100%", padding: 12, marginTop: 12, background: "#0ea5e9", color: "white", border: "none", borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: "pointer" },
  btnSecondary: { padding: "8px 14px", background: "white", color: "#0f172a", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: 13, fontWeight: 500, cursor: "pointer" },
  error: { background: "#fee2e2", color: "#991b1b", padding: 12, borderRadius: 8, marginBottom: 16 },
  kpiGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12, marginBottom: 24 },
  kpi: { background: "white", padding: 16, borderRadius: 8, border: "1px solid #e2e8f0" },
  kpiHighlight: { background: "linear-gradient(135deg, #0ea5e9, #6366f1)", color: "white", border: "none" },
  kpiLabel: { fontSize: 12, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5 },
  kpiValue: { fontSize: 24, fontWeight: 700, marginTop: 4 },
  card: { background: "white", padding: 20, borderRadius: 8, border: "1px solid #e2e8f0", marginBottom: 16 },
  h2: { margin: "0 0 12px 0", fontSize: 16 },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 13 },
  tierPill: { display: "inline-block", padding: "2px 8px", borderRadius: 12, color: "white", fontSize: 11, fontWeight: 600, textTransform: "uppercase" },
  modalBg: { position: "fixed", inset: 0, background: "rgba(15,23,42,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 },
  modal: { background: "white", padding: 24, borderRadius: 12, width: 600, maxWidth: "92vw", maxHeight: "90vh", overflowY: "auto" },
  dl: { display: "grid", gridTemplateColumns: "180px 1fr", gap: "4px 16px", fontSize: 13, marginBottom: 16 },
};
