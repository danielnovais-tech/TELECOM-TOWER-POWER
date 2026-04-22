const PRICING = {
  pt: {
    title: "Preços claros, sem surpresa",
    sub: "Comece grátis. Escale quando precisar. Cancele quando quiser.",
    period: "/mês",
    periodAnnual: "/mês, cobrado anualmente",
    from: "a partir de",
    toggleM: "Mensal",
    toggleA: "Anual (−17%)",
    cta: { free: "Começar grátis", paid: "Assinar agora", enterprise: "Falar com vendas" },
    compare: "Todos os planos incluem:",
    included: ["API REST", "SDKs Python & JS", "Base ANATEL + OpenCelliD", "SRTM 90m com cache Redis", "Portal de uso e chaves", "Suporte por email"],
    tiers: [
      { id: "free", name: "Free", price: "R$ 0", priceAnnual: "R$ 0", bullets: ["200 chamadas/mês", "Até 20 torres por consulta", "5 PDFs/mês", "Sem IA", "Comunidade GitHub"], highlight: false },
      { id: "starter", name: "Starter", price: "R$ 79", priceAnnual: "R$ 65", bullets: ["3.000 chamadas/mês", "Até 100 torres por consulta", "50 PDFs/mês", "Lote até 100 receptores", "Suporte 48h"], highlight: false },
      { id: "pro", name: "Pro", price: "R$ 349", priceAnnual: "R$ 289", bullets: ["25.000 chamadas/mês", "Até 500 torres por consulta", "500 PDFs/mês + IA", "Lote até 2.000 receptores", "Suporte 24h"], highlight: true, badge: "Mais popular" },
      { id: "business", name: "Business", price: "R$ 1.299", priceAnnual: "R$ 1.079", bullets: ["150.000 chamadas/mês", "Lote até 5.000 receptores", "5.000 PDFs/mês", "Fila prioritária + IA ilimitada", "Suporte em até 4h"], highlight: false },
      { id: "enterprise", name: "Enterprise", price: "sob consulta", priceAnnual: "sob consulta", bullets: ["Volume customizado", "SLA 99.95%", "Redis dedicado", "SSO SAML + IP allowlist", "Suporte 24/7 + slack compartilhado"], highlight: false, custom: true }
    ]
  },
  en: {
    title: "Straightforward pricing, no surprises",
    sub: "Start free. Scale when you need it. Cancel anytime.",
    period: "/mo",
    periodAnnual: "/mo, billed annually",
    from: "from",
    toggleM: "Monthly",
    toggleA: "Annual (−17%)",
    cta: { free: "Start free", paid: "Subscribe", enterprise: "Talk to sales" },
    compare: "Every plan includes:",
    included: ["REST API", "Python & JS SDKs", "ANATEL + OpenCelliD database", "SRTM 90m with Redis cache", "Usage & key portal", "Email support"],
    tiers: [
      { id: "free", name: "Free", price: "R$ 0", priceAnnual: "R$ 0", bullets: ["200 calls/mo", "Up to 20 towers/query", "5 PDFs/month", "No AI", "GitHub community"], highlight: false },
      { id: "starter", name: "Starter", price: "R$ 79", priceAnnual: "R$ 65", bullets: ["3,000 calls/mo", "Up to 100 towers/query", "50 PDFs/month", "Batch up to 100 receivers", "48h support"], highlight: false },
      { id: "pro", name: "Pro", price: "R$ 349", priceAnnual: "R$ 289", bullets: ["25,000 calls/mo", "Up to 500 towers/query", "500 PDFs/mo + AI", "Batch up to 2,000 receivers", "24h support"], highlight: true, badge: "Most popular" },
      { id: "business", name: "Business", price: "R$ 1,299", priceAnnual: "R$ 1,079", bullets: ["150,000 calls/mo", "Batch up to 5,000 receivers", "5,000 PDFs/mo", "Priority queue + unlimited AI", "4h response support"], highlight: false },
      { id: "enterprise", name: "Enterprise", price: "custom", priceAnnual: "custom", bullets: ["Custom volume", "99.95% SLA", "Dedicated Redis", "SAML SSO + IP allowlist", "24/7 support + shared Slack"], highlight: false, custom: true }
    ]
  }
};

import { useState } from "react";

export default function Pricing({ lang = "pt", onSignup }) {
  const [cycle, setCycle] = useState("monthly");
  const t = PRICING[lang] || PRICING.pt;
  const annual = cycle === "annual";

  return (
    <div>
      <h2 style={{ fontSize: 36, fontWeight: 700, textAlign: "center", marginBottom: 8 }}>{t.title}</h2>
      <p style={{ textAlign: "center", color: "#475569", marginBottom: 24 }}>{t.sub}</p>

      <div style={{ display: "flex", justifyContent: "center", marginBottom: 32 }}>
        <div style={{ background: "#f1f5f9", padding: 4, borderRadius: 999, display: "inline-flex" }}>
          <button
            onClick={() => setCycle("monthly")}
            style={{
              background: !annual ? "#fff" : "transparent",
              border: "none",
              borderRadius: 999,
              padding: "8px 18px",
              fontWeight: 600,
              fontSize: 13,
              cursor: "pointer",
              boxShadow: !annual ? "0 1px 3px rgba(0,0,0,.08)" : "none"
            }}
          >
            {t.toggleM}
          </button>
          <button
            onClick={() => setCycle("annual")}
            style={{
              background: annual ? "#fff" : "transparent",
              border: "none",
              borderRadius: 999,
              padding: "8px 18px",
              fontWeight: 600,
              fontSize: 13,
              cursor: "pointer",
              boxShadow: annual ? "0 1px 3px rgba(0,0,0,.08)" : "none"
            }}
          >
            {t.toggleA}
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
        {t.tiers.map((tier) => (
          <div key={tier.id} style={{
            background: "#fff",
            border: tier.highlight ? "2px solid #0369a1" : "1px solid #e2e8f0",
            borderRadius: 12,
            padding: 24,
            position: "relative",
            boxShadow: tier.highlight ? "0 8px 24px rgba(3,105,161,.15)" : "none"
          }}>
            {tier.badge && (
              <div style={{ position: "absolute", top: -12, left: "50%", transform: "translateX(-50%)", background: "#0369a1", color: "#fff", fontSize: 11, padding: "4px 10px", borderRadius: 999, fontWeight: 600 }}>
                {tier.badge}
              </div>
            )}
            <h3 style={{ fontSize: 16, fontWeight: 700, margin: "0 0 4px" }}>{tier.name}</h3>
            <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: 4 }}>
              {tier.custom && <span style={{ fontSize: 12, color: "#64748b" }}>{t.from}</span>}
              <span style={{ fontSize: 28, fontWeight: 800 }}>{annual ? tier.priceAnnual : tier.price}</span>
              <span style={{ fontSize: 13, color: "#64748b" }}>{t.period}</span>
            </div>
            {annual && !tier.custom && tier.id !== "free" && (
              <div style={{ fontSize: 11, color: "#64748b", marginBottom: 12 }}>{t.periodAnnual}</div>
            )}
            <ul style={{ listStyle: "none", padding: 0, margin: "12px 0 24px", fontSize: 13, color: "#334155" }}>
              {tier.bullets.map((b, i) => (
                <li key={i} style={{ padding: "6px 0", borderBottom: "1px dashed #e2e8f0" }}>✓ {b}</li>
              ))}
            </ul>
            <button
              onClick={() => onSignup && onSignup(tier.id, annual ? "annual" : "monthly")}
              style={{
                width: "100%",
                background: tier.highlight ? "#0f172a" : "#fff",
                color: tier.highlight ? "#fff" : "#0f172a",
                border: tier.highlight ? "none" : "1px solid #0f172a",
                borderRadius: 8,
                padding: "10px 14px",
                cursor: "pointer",
                fontWeight: 600,
                fontSize: 14
              }}
            >
              {tier.id === "free" ? t.cta.free : tier.custom ? t.cta.enterprise : t.cta.paid}
            </button>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 48, textAlign: "center", color: "#475569", fontSize: 13 }}>
        <strong>{t.compare}</strong>{" "}
        {t.included.join(" · ")}
      </div>
    </div>
  );
}
