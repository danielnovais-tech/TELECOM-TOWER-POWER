// Comparativo TTP vs alternativas — escalada de economia anual por porte BR.
// Ver detalhes em docs-site/docs/case-studies/ttp-vs-alternatives.md

const COPY = {
  pt: {
    title: "Quanto a economia escala com o porte da empresa",
    sub: "Comparativo bottom-up: custo anual sem TTP × custo anual com TTP, para perfis típicos do mercado brasileiro 2026.",
    headers: {
      tier: "Porte",
      stackBefore: "Stack atual típico",
      costBefore: "Custo/ano sem TTP",
      stackAfter: "Com TTP",
      costAfter: "Custo/ano com TTP",
      savings: "Economia/ano"
    },
    rows: [
      {
        tier: "WISP regional",
        sub: "5–30k assinantes",
        stackBefore: "Google Earth + planilha + 60 visitas",
        costBefore: 162000,
        stackAfter: "TTP Pro + 20 visitas",
        costAfter: 49000,
        savings: 113000
      },
      {
        tier: "Consultoria RF",
        sub: "1–3 consultores",
        stackBefore: "Pathloss + planilha (capacidade limitada)",
        costBefore: 223000,
        stackAfter: "Pathloss + TTP Business (3× capacidade)",
        costAfter: 339000,
        savings: 105000,
        note: "margem extra (alavancagem de receita)"
      },
      {
        tier: "ISP regional grande",
        sub: "50–200k assinantes",
        stackBefore: "2 FTE eng. + planilha + 200 visitas",
        costBefore: 950000,
        stackAfter: "TTP Enterprise + 0,5 FTE + 60 visitas",
        costAfter: 290000,
        savings: 660000
      },
      {
        tier: "Tier-2/3 regional",
        sub: "Algar, Sercomtel, Brisanet",
        stackBefore: "Atoll + consultoria externa",
        costBefore: 450000,
        stackAfter: "Atoll + TTP Ultra (substitui consultoria)",
        costAfter: 225000,
        savings: 225000
      },
      {
        tier: "Tier-1 nacional",
        sub: "Vivo, TIM, Claro",
        stackBefore: "Atoll + Planet + CelPlan + equipe interna",
        costBefore: 8000000,
        stackAfter: "TTP não substitui este stack",
        costAfter: 8000000,
        savings: 0,
        note: "fora do ICP"
      }
    ],
    footer:
      "Atoll, Planet, iBwave e CelPlan são padrão Tier-1 e fazem o que TTP não pretende fazer (ray-tracing 5G mmWave, MOCN, MIMO). TTP é a opção certa para planejamento estratégico, scouting e laudo regulatório em larga escala.",
    cta: "Ver comparativo completo →",
    ctaUrl: "https://docs.telecomtowerpower.com.br/case-studies/ttp-vs-alternatives/"
  },
  en: {
    title: "How savings scale with company size",
    sub: "Bottom-up comparison: annual cost without TTP × with TTP, for typical Brazilian market profiles (2026).",
    headers: {
      tier: "Size",
      stackBefore: "Typical current stack",
      costBefore: "Annual cost without TTP",
      stackAfter: "With TTP",
      costAfter: "Annual cost with TTP",
      savings: "Savings/year"
    },
    rows: [
      {
        tier: "Regional WISP",
        sub: "5–30k subscribers",
        stackBefore: "Google Earth + spreadsheet + 60 site visits",
        costBefore: 162000,
        stackAfter: "TTP Pro + 20 site visits",
        costAfter: 49000,
        savings: 113000
      },
      {
        tier: "RF consultancy",
        sub: "1–3 consultants",
        stackBefore: "Pathloss + spreadsheet (capacity-limited)",
        costBefore: 223000,
        stackAfter: "Pathloss + TTP Business (3× capacity)",
        costAfter: 339000,
        savings: 105000,
        note: "extra margin (revenue leverage)"
      },
      {
        tier: "Large regional ISP",
        sub: "50–200k subscribers",
        stackBefore: "2 FTE engineers + spreadsheet + 200 visits",
        costBefore: 950000,
        stackAfter: "TTP Enterprise + 0.5 FTE + 60 visits",
        costAfter: 290000,
        savings: 660000
      },
      {
        tier: "Tier-2/3 regional carrier",
        sub: "Algar, Sercomtel, Brisanet",
        stackBefore: "Atoll + external consulting",
        costBefore: 450000,
        stackAfter: "Atoll + TTP Ultra (replaces consulting)",
        costAfter: 225000,
        savings: 225000
      },
      {
        tier: "Tier-1 national",
        sub: "Vivo, TIM, Claro",
        stackBefore: "Atoll + Planet + CelPlan + internal team",
        costBefore: 8000000,
        stackAfter: "TTP does not replace this stack",
        costAfter: 8000000,
        savings: 0,
        note: "out of ICP"
      }
    ],
    footer:
      "Atoll, Planet, iBwave and CelPlan are Tier-1 standards and do what TTP doesn't aim to do (5G mmWave ray-tracing, MOCN, MIMO). TTP is the right call for strategic planning, scouting and regulatory filings at scale.",
    cta: "See full comparison →",
    ctaUrl: "https://docs.telecomtowerpower.com.br/en/case-studies/ttp-vs-alternatives/"
  }
};

function fmtBRL(n, lang) {
  return n.toLocaleString(lang === "en" ? "en-US" : "pt-BR", {
    style: "currency",
    currency: "BRL",
    maximumFractionDigits: 0
  });
}

export default function SavingsLadder({ lang = "pt" }) {
  const t = COPY[lang] || COPY.pt;
  const maxSavings = Math.max(...t.rows.map((r) => r.savings)) || 1;

  return (
    <div>
      <h2 style={styles.h2}>{t.title}</h2>
      <p style={styles.sub}>{t.sub}</p>

      <div style={styles.tableWrap}>
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>{t.headers.tier}</th>
              <th style={styles.th}>{t.headers.stackBefore}</th>
              <th style={styles.thRight}>{t.headers.costBefore}</th>
              <th style={styles.th}>{t.headers.stackAfter}</th>
              <th style={styles.thRight}>{t.headers.costAfter}</th>
              <th style={styles.thRight}>{t.headers.savings}</th>
            </tr>
          </thead>
          <tbody>
            {t.rows.map((r, i) => {
              const barPct = Math.round((r.savings / maxSavings) * 100);
              const isOutOfIcp = r.savings === 0;
              return (
                <tr key={i} style={{ ...styles.tr, ...(isOutOfIcp ? styles.trMuted : {}) }}>
                  <td style={styles.td}>
                    <div style={styles.tierName}>{r.tier}</div>
                    <div style={styles.tierSub}>{r.sub}</div>
                  </td>
                  <td style={styles.tdSmall}>{r.stackBefore}</td>
                  <td style={styles.tdRight}>{fmtBRL(r.costBefore, lang)}</td>
                  <td style={styles.tdSmall}>{r.stackAfter}</td>
                  <td style={styles.tdRight}>{fmtBRL(r.costAfter, lang)}</td>
                  <td style={styles.tdSavings}>
                    <div style={styles.barWrap}>
                      <div
                        style={{
                          ...styles.bar,
                          width: `${barPct}%`,
                          background: isOutOfIcp ? "#cbd5e1" : "#16a34a"
                        }}
                      />
                    </div>
                    <div style={{ ...styles.savingsValue, color: isOutOfIcp ? "#94a3b8" : "#15803d" }}>
                      {isOutOfIcp ? "—" : fmtBRL(r.savings, lang)}
                    </div>
                    {r.note && <div style={styles.noteText}>{r.note}</div>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p style={styles.footer}>{t.footer}</p>
      <p style={styles.ctaWrap}>
        <a href={t.ctaUrl} style={styles.cta}>{t.cta}</a>
      </p>
    </div>
  );
}

const styles = {
  h2: { fontSize: 36, fontWeight: 700, textAlign: "center", marginBottom: 8 },
  sub: { textAlign: "center", color: "#475569", marginBottom: 32, maxWidth: 760, marginLeft: "auto", marginRight: "auto" },
  tableWrap: { maxWidth: 1120, margin: "0 auto", overflowX: "auto" },
  table: { width: "100%", borderCollapse: "collapse", background: "#fff", borderRadius: 12, boxShadow: "0 8px 24px rgba(15,23,42,.06)", overflow: "hidden" },
  th: { textAlign: "left", padding: "14px 16px", fontSize: 12, fontWeight: 700, color: "#475569", textTransform: "uppercase", letterSpacing: 0.5, background: "#f1f5f9", borderBottom: "1px solid #e2e8f0" },
  thRight: { textAlign: "right", padding: "14px 16px", fontSize: 12, fontWeight: 700, color: "#475569", textTransform: "uppercase", letterSpacing: 0.5, background: "#f1f5f9", borderBottom: "1px solid #e2e8f0" },
  tr: { borderBottom: "1px solid #f1f5f9" },
  trMuted: { opacity: 0.55 },
  td: { padding: "14px 16px", verticalAlign: "top", fontSize: 14, color: "#0f172a" },
  tdSmall: { padding: "14px 16px", verticalAlign: "top", fontSize: 13, color: "#475569", maxWidth: 200 },
  tdRight: { padding: "14px 16px", verticalAlign: "top", fontSize: 14, color: "#0f172a", textAlign: "right", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" },
  tdSavings: { padding: "14px 16px", verticalAlign: "top", textAlign: "right", minWidth: 180 },
  tierName: { fontWeight: 700, fontSize: 14 },
  tierSub: { fontSize: 12, color: "#64748b", marginTop: 2 },
  barWrap: { width: "100%", height: 8, background: "#f1f5f9", borderRadius: 999, overflow: "hidden", marginBottom: 6 },
  bar: { height: "100%", borderRadius: 999, transition: "width .4s" },
  savingsValue: { fontSize: 16, fontWeight: 800, fontVariantNumeric: "tabular-nums" },
  noteText: { fontSize: 11, color: "#94a3b8", fontStyle: "italic", marginTop: 4 },
  footer: { maxWidth: 880, margin: "32px auto 12px", textAlign: "center", fontSize: 13, color: "#64748b", lineHeight: 1.6 },
  ctaWrap: { textAlign: "center", marginTop: 8 },
  cta: { color: "#0369a1", fontWeight: 700, textDecoration: "none", fontSize: 14 }
};
