import { useState } from "react";

// Premissas (R$/ano). Espelham docs-site/docs/case-studies/roi-by-segment.md
const SEGMENTS = {
  pt: [
    {
      id: "wisp",
      label: "WISP regional (5–30 mil assinantes)",
      plan: "Pro · R$ 349/mês",
      annualCost: 4188,
      grossSavings: 117000,
      bullets: [
        "Reduz scouting físico de ~60 para ~20 visitas/ano",
        "Tempo de eng. em planilha cai 75%",
        "Menos pareceres rejeitados na ANATEL"
      ]
    },
    {
      id: "rf",
      label: "Consultoria RF (1–3 consultores)",
      plan: "Business · R$ 1.299/mês",
      annualCost: 15588,
      grossSavings: 120000,
      bullets: [
        "Tempo por laudo cai de 6–12h para 2–4h",
        "Atende 3× mais clientes com a mesma equipe",
        "Pathloss segue licenciado, TTP complementa"
      ]
    },
    {
      id: "isp",
      label: "ISP regional grande (50–200 mil assinantes)",
      plan: "Enterprise · R$ 1.890/mês",
      annualCost: 22680,
      grossSavings: 685000,
      bullets: [
        "1,5 FTE liberado (eng. RF) por ano",
        "Scouting de torres reduz de 200 para 60 visitas/ano",
        "Atrasos em homologação caem em ~70%"
      ]
    },
    {
      id: "regional",
      label: "Tier-2/3 regional (Algar, Sercomtel, Brisanet)",
      plan: "Ultra · R$ 2.900/mês",
      annualCost: 34800,
      grossSavings: 260000,
      bullets: [
        "Complementa Atoll para projetos táticos rurais",
        "Reduz consultoria externa em leilões / regulatório",
        "Modelo BR-calibrado preenche gap do clutter Atoll"
      ]
    }
  ],
  en: [
    {
      id: "wisp",
      label: "Regional WISP (5–30k subscribers)",
      plan: "Pro · R$ 349/mo",
      annualCost: 4188,
      grossSavings: 117000,
      bullets: [
        "Cuts physical scouting from ~60 to ~20 trips/year",
        "Engineer spreadsheet time drops 75%",
        "Fewer ANATEL filings rejected"
      ]
    },
    {
      id: "rf",
      label: "RF consultancy (1–3 consultants)",
      plan: "Business · R$ 1,299/mo",
      annualCost: 15588,
      grossSavings: 120000,
      bullets: [
        "Time per report drops from 6–12h to 2–4h",
        "Serve 3× more clients with the same team",
        "Pathloss stays licensed; TTP complements it"
      ]
    },
    {
      id: "isp",
      label: "Large regional ISP (50–200k subscribers)",
      plan: "Enterprise · R$ 1,890/mo",
      annualCost: 22680,
      grossSavings: 685000,
      bullets: [
        "1.5 FTE freed (RF engineering) per year",
        "Tower scouting drops from 200 to 60 trips/year",
        "Filing delays cut by ~70%"
      ]
    },
    {
      id: "regional",
      label: "Tier-2/3 regional carrier (Algar, Sercomtel, Brisanet)",
      plan: "Ultra · R$ 2,900/mo",
      annualCost: 34800,
      grossSavings: 260000,
      bullets: [
        "Complements Atoll for tactical rural projects",
        "Cuts external consulting on auctions / regulatory work",
        "BR-calibrated model fills Atoll clutter gaps"
      ]
    }
  ]
};

const COPY = {
  pt: {
    title: "Quanto sua equipe economiza por ano",
    sub: "Estimativa bottom-up baseada em custo-hora e benchmarks de mercado BR 2026. Selecione o perfil mais próximo ao seu.",
    netLabel: "Economia líquida estimada",
    roiLabel: "ROI",
    paybackLabel: "Payback",
    paybackUnit: "dias",
    costLabel: "Custo TTP",
    grossLabel: "Economia bruta",
    perYear: "/ano",
    disclaimer: "Estimativa não-contratual. Ver premissas em docs.telecomtowerpower.com.br/case-studies/roi-by-segment/"
  },
  en: {
    title: "How much your team saves per year",
    sub: "Bottom-up estimate based on hour-cost and BR market benchmarks (2026). Pick the profile closest to yours.",
    netLabel: "Estimated net savings",
    roiLabel: "ROI",
    paybackLabel: "Payback",
    paybackUnit: "days",
    costLabel: "TTP cost",
    grossLabel: "Gross savings",
    perYear: "/year",
    disclaimer: "Non-contractual estimate. Assumptions at docs.telecomtowerpower.com.br/en/case-studies/roi-by-segment/"
  }
};

function fmtBRL(n, lang) {
  return n.toLocaleString(lang === "en" ? "en-US" : "pt-BR", {
    style: "currency",
    currency: "BRL",
    maximumFractionDigits: 0
  });
}

export default function RoiCalculator({ lang = "pt" }) {
  const t = COPY[lang] || COPY.pt;
  const segments = SEGMENTS[lang] || SEGMENTS.pt;
  const [selected, setSelected] = useState(segments[0].id);
  const seg = segments.find((s) => s.id === selected) || segments[0];
  const net = seg.grossSavings - seg.annualCost;
  const roi = Math.round(seg.grossSavings / seg.annualCost);
  const paybackDays = Math.max(1, Math.round((seg.annualCost / seg.grossSavings) * 365));

  return (
    <div>
      <h2 style={styles.h2}>{t.title}</h2>
      <p style={styles.sub}>{t.sub}</p>

      <div style={styles.tabs}>
        {segments.map((s) => (
          <button
            key={s.id}
            onClick={() => setSelected(s.id)}
            style={{
              ...styles.tab,
              ...(s.id === selected ? styles.tabActive : {})
            }}
          >
            {s.label}
          </button>
        ))}
      </div>

      <div style={styles.panel}>
        <div style={styles.kpiRow}>
          <div style={styles.kpiBig}>
            <div style={styles.kpiLabel}>{t.netLabel}</div>
            <div style={styles.kpiValueBig}>{fmtBRL(net, lang)}<span style={styles.perYear}>{t.perYear}</span></div>
            <div style={styles.kpiSubGrid}>
              <span><strong>{t.roiLabel}:</strong> {roi}×</span>
              <span><strong>{t.paybackLabel}:</strong> {paybackDays} {t.paybackUnit}</span>
            </div>
          </div>
          <div style={styles.kpiSmallCol}>
            <div style={styles.kpiSmall}>
              <div style={styles.kpiLabel}>{t.grossLabel}</div>
              <div style={styles.kpiValueSmall}>{fmtBRL(seg.grossSavings, lang)}</div>
            </div>
            <div style={styles.kpiSmall}>
              <div style={styles.kpiLabel}>{t.costLabel}</div>
              <div style={styles.kpiValueSmall}>−{fmtBRL(seg.annualCost, lang)}</div>
              <div style={styles.kpiPlan}>{seg.plan}</div>
            </div>
          </div>
        </div>

        <ul style={styles.bullets}>
          {seg.bullets.map((b, i) => <li key={i}>{b}</li>)}
        </ul>

        <p style={styles.disclaimer}>{t.disclaimer}</p>
      </div>
    </div>
  );
}

const styles = {
  h2: { fontSize: 36, fontWeight: 700, textAlign: "center", marginBottom: 8 },
  sub: { textAlign: "center", color: "#475569", marginBottom: 32, maxWidth: 720, marginLeft: "auto", marginRight: "auto" },
  tabs: { display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center", marginBottom: 24 },
  tab: {
    background: "#fff",
    border: "1px solid #cbd5e1",
    borderRadius: 999,
    padding: "8px 16px",
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
    color: "#334155"
  },
  tabActive: { background: "#0f172a", color: "#fff", borderColor: "#0f172a" },
  panel: {
    maxWidth: 960,
    margin: "0 auto",
    background: "#fff",
    border: "1px solid #e2e8f0",
    borderRadius: 16,
    padding: 32,
    boxShadow: "0 8px 24px rgba(15,23,42,.06)"
  },
  kpiRow: { display: "grid", gridTemplateColumns: "2fr 1fr", gap: 24, marginBottom: 24 },
  kpiBig: {
    background: "linear-gradient(135deg, #0369a1 0%, #0f172a 100%)",
    color: "#fff",
    borderRadius: 12,
    padding: 24
  },
  kpiLabel: { fontSize: 12, opacity: 0.85, textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 8, fontWeight: 600 },
  kpiValueBig: { fontSize: 40, fontWeight: 800, lineHeight: 1.1 },
  perYear: { fontSize: 16, fontWeight: 500, marginLeft: 6, opacity: 0.85 },
  kpiSubGrid: { display: "flex", gap: 24, marginTop: 16, fontSize: 14 },
  kpiSmallCol: { display: "flex", flexDirection: "column", gap: 12 },
  kpiSmall: { background: "#f8fafc", borderRadius: 10, padding: 16 },
  kpiValueSmall: { fontSize: 22, fontWeight: 700, color: "#0f172a" },
  kpiPlan: { fontSize: 12, color: "#64748b", marginTop: 4 },
  bullets: { margin: 0, paddingLeft: 20, color: "#334155", fontSize: 15, lineHeight: 1.8 },
  disclaimer: { fontSize: 12, color: "#94a3b8", marginTop: 20, textAlign: "center", fontStyle: "italic" }
};
