import { useState } from "react";
import LoginWithSSO from "./LoginWithSSO";
import Pricing from "./Pricing";
import RoiCalculator from "./RoiCalculator";
import SavingsLadder from "./SavingsLadder";

const COPY = {
  pt: {
    nav: { features: "Recursos", pricing: "Preços", docs: "Docs", faq: "FAQ", login: "Entrar", signup: "Criar conta" },
    hero: {
      eyebrow: "API de planejamento de rádio para o Brasil",
      title: "Planejamento de enlaces em segundos, com dados ANATEL ao vivo",
      sub: "140.906 torres georreferenciadas, terreno SRTM real e análise de link com IA — tudo em uma API REST. Substitua planilhas e licenças perpétuas por uma assinatura mensal.",
      cta1: "Teste a API agora",
      cta2: "Ver preços",
      badge: "Dados atualizados com a Consulta Anatel SMP/SME"
    },
    stats: [
      { n: "140.906", l: "torres na base" },
      { n: "5.570", l: "municípios cobertos" },
      { n: "12", l: "prestadoras indexadas" },
      { n: "< 400 ms", l: "latência p50 cacheada" }
    ],
    features: {
      title: "Tudo que um ISP ou consultor RF precisa",
      items: [
        { t: "Base ANATEL + OpenCelliD", d: "105.240 ERBs oficiais Anatel + dados comunitários OpenCelliD, sincronizados diariamente, com snap por prestadora (Vivo, Claro, TIM, Algar, Sercomtel etc.)." },
        { t: "Terreno SRTM real", d: "Perfil de elevação ponto-a-ponto via SRTM 90 m, com cache Redis em 4 camadas e latência sub-segundo." },
        { t: "Análise de link com IA", d: "Explicação em linguagem natural de perda de percurso, Fresnel e margem via Amazon Bedrock (Claude)." },
        { t: "Modelo ML calibrado", d: "Ridge-v1 com 17 features (SRTM, Fresnel, rugosidade) treinado contra RSSI real — RMSE 12,94 dB em n=20 000, supera Hata físico-puro." },
        { t: "Repetidores multi-saltos", d: "Encontre a cadeia ótima de torres para atender um receptor fora da linha de visada principal." },
        { t: "PDF institucional", d: "Relatório completo com mapa, perfil do terreno e parecer técnico pronto para cliente ou ANATEL." },
        { t: "SDKs Python e JS", d: "Clientes gerados do OpenAPI, prontos para integrar em suas ferramentas internas." }
      ]
    },
    useCases: {
      title: "Para quem é",
      items: [
        { t: "WISPs em expansão", d: "Descubra antes de subir a torre se aquele novo bairro está em linha de visada. Economize viagens de campo." },
        { t: "Consultores de RF", d: "Emita pareceres técnicos em minutos. Atenda 3× mais clientes sem comprar Pathloss ou Atoll." },
        { t: "Integradoras", d: "Cobertura preliminar para propostas comerciais antes mesmo de fechar contrato." },
        { t: "Órgãos públicos e LAI", d: "Responda pedidos de acesso à informação com mapas de cobertura oficiais em 5 minutos." }
      ]
    },
    scope: {
      title: "Escopo & limitações",
      sub: "Honestidade técnica vale mais do que marketing. Eis o que fazemos — e o que não fazemos.",
      doTitle: "Fazemos",
      do: [
        "Enlaces ponto-a-ponto e ponto-multiponto outdoor licenciados",
        "Free-Space Path Loss + correção de relevo SRTM 90 m + 1ª zona de Fresnel",
        "Plano de repetidores multi-saltos sobre torres ANATEL",
        "Estimativa preliminar de cobertura para WISPs / ISPs regionais",
        "Pareceres técnicos em PDF e exportação KML/Shapefile"
      ],
      dontTitle: "Não fazemos (use Atoll, Planet, Asset ou CelPlan)",
      dont: [
        "Planejamento RAN macro/indoor/5G mmWave para operadoras Tier-1",
        "Simulação de tráfego, capacidade e interferência multi-célula",
        "Drive-test analytics e calibração de modelo por medição",
        "Modelos Longley-Rice (roadmap Q3/2026) e ITU-R P.1812 fora de beta",
        "Substituir Atoll, Planet ou CelPlan em operações de Tier-1"
      ]
    },
    faq: {
      title: "Perguntas frequentes",
      items: [
        { q: "De onde vêm os dados das torres?", a: "105.240 torres vêm do sistema MOSAICO da Anatel (licenciamento SMP/SME) atualizado mensalmente. Complementamos com OpenCelliD para células não-licenciadas e sites internacionais." },
        { q: "O modelo de propagação é o quê?", a: "Free-Space Path Loss + correção de terreno SRTM com cheque da primeira zona de Fresnel. Para cenários NLOS usamos ITU-R P.1812 (beta). Não é Longley-Rice ainda — roadmap Q3/2026." },
        { q: "Posso integrar na minha plataforma de OSS/BSS?", a: "Sim. API REST + webhooks Stripe + SDKs Python/JS. Enterprise inclui IP allowlist e SSO SAML." },
        { q: "E LGPD?", a: "Dados de torres são públicos (Anatel). Não coletamos dados pessoais de usuários finais. Logs de API ficam retidos 30 dias." },
        { q: "Cancelamento?", a: "A qualquer momento via portal. Sem multa, sem fidelidade." }
      ]
    },
    footer: {
      tagline: "Feito em Brasília · © TELECOM TOWER POWER 2026",
      support: "Suporte",
      sales: "Vendas",
      status: "Status",
      terms: "Termos",
      privacy: "Privacidade",
      refund: "Reembolso",
      security: "Segurança",
      reliability: "Confiabilidade",
      contact: "Contato"
    }
  },
  en: {
    nav: { features: "Features", pricing: "Pricing", docs: "Docs", faq: "FAQ", login: "Sign in", signup: "Sign up" },
    hero: {
      eyebrow: "Radio planning API for Brazil",
      title: "Link planning in seconds, backed by live ANATEL data",
      sub: "140,498 geolocated towers, real SRTM terrain and AI-powered link analysis — all from a REST API. Replace spreadsheets and perpetual licenses with a monthly subscription.",
      cta1: "Try the API",
      cta2: "See pricing",
      badge: "Data synced from the ANATEL SMP/SME registry"
    },
    stats: [
      { n: "140,498", l: "towers indexed" },
      { n: "5,570", l: "municipalities" },
      { n: "12", l: "SMP/SME providers" },
      { n: "< 400 ms", l: "cached p50" }
    ],
    features: {
      title: "Everything an ISP or RF consultant needs",
      items: [
        { t: "ANATEL + OpenCelliD DB", d: "105,240 officially licensed Anatel sites plus crowd-sourced OpenCelliD cells, refreshed daily, snapped per provider (Vivo, Claro, TIM, Algar, Sercomtel, etc.)." },
        { t: "Real SRTM terrain", d: "Point-to-point elevation profile from 90m SRTM with a 4-layer Redis cache and sub-second latency." },
        { t: "AI link analysis", d: "Natural-language explanation of path loss, Fresnel clearance and link margin via Amazon Bedrock (Claude)." },
        { t: "Calibrated ML model", d: "Ridge-v1 with 17 features (SRTM, Fresnel, roughness) trained on real RSSI — RMSE 12.94 dB at n=20,000, beats raw Hata." },
        { t: "Multi-hop repeaters", d: "Find the optimal chain of existing towers to serve a receiver outside the main line of sight." },
        { t: "Branded PDF report", d: "Map, terrain profile and technical statement — deliverable to clients or regulators." },
        { t: "Python and JS SDKs", d: "OpenAPI-generated clients, ready to drop into your internal tooling." }
      ]
    },
    useCases: {
      title: "Who it is for",
      items: [
        { t: "Growing WISPs", d: "Know before the truck rolls whether that new neighborhood is in line-of-sight. Skip scouting trips." },
        { t: "RF consultants", d: "Ship technical reports in minutes. Serve 3× more clients without buying Pathloss or Atoll." },
        { t: "System integrators", d: "Preliminary coverage estimates for commercial proposals before the contract is signed." },
        { t: "Government & FOIA", d: "Answer public information requests with official coverage maps in under 5 minutes." }
      ]
    },
    scope: {
      title: "Scope & limitations",
      sub: "Technical honesty beats marketing. Here is what we do — and what we don't.",
      doTitle: "What we do",
      do: [
        "Licensed outdoor point-to-point and point-to-multipoint links",
        "Free-space path loss + 90 m SRTM terrain correction + first Fresnel zone",
        "Multi-hop repeater planning across ANATEL towers",
        "Preliminary coverage estimates for WISPs / regional ISPs",
        "PDF technical reports and KML/Shapefile export"
      ],
      dontTitle: "What we don't do (use Atoll, Planet, Asset or CelPlan)",
      dont: [
        "Macro RAN / indoor / 5G mmWave planning for Tier-1 operators",
        "Traffic, capacity and multi-cell interference simulation",
        "Drive-test analytics and measurement-based model calibration",
        "Longley-Rice (roadmap Q3/2026) and out-of-beta ITU-R P.1812",
        "Replacing Atoll, Planet or CelPlan in Tier-1 operations"
      ]
    },
    faq: {
      title: "FAQ",
      items: [
        { q: "Where does tower data come from?", a: "105,240 towers come from Anatel's MOSAICO system (SMP/SME licensing), refreshed monthly. We enrich with OpenCelliD for unlicensed cells and international sites." },
        { q: "What propagation model is used?", a: "Free-Space Path Loss plus SRTM terrain correction with first-Fresnel clearance check. For NLOS we use ITU-R P.1812 (beta). Longley-Rice is on the Q3/2026 roadmap." },
        { q: "Can I integrate with my OSS/BSS?", a: "Yes — REST API, Stripe webhooks and Python/JS SDKs. Enterprise adds IP allowlist and SAML SSO." },
        { q: "LGPD/GDPR?", a: "Tower data is public (Anatel). We never collect end-user personal data. API logs are retained for 30 days." },
        { q: "Cancellation?", a: "Anytime via the self-service portal. No fees, no lock-in." }
      ]
    },
    footer: {
      tagline: "Built in Brasília · © TELECOM TOWER POWER 2026",
      support: "Support",
      sales: "Sales",
      status: "Status",
      terms: "Terms",
      privacy: "Privacy",
      refund: "Refunds",
      security: "Security",
      reliability: "Reliability",
      contact: "Contact"
    }
  }
};

export default function Landing({ onSignup, onLogin }) {
  const [lang, setLang] = useState(() =>
    (navigator.language || "pt").toLowerCase().startsWith("pt") ? "pt" : "en"
  );
  const t = COPY[lang];

  return (
    <div style={{ fontFamily: "Inter, system-ui, sans-serif", color: "#0f172a", background: "#fff" }}>
      <header style={styles.nav}>
        <div style={styles.navInner}>
          <div style={styles.brand}>📡 TELECOM TOWER POWER</div>
          <nav style={styles.navLinks}>
            <a href="#features">{t.nav.features}</a>
            <a href="#pricing">{t.nav.pricing}</a>
            <a href="https://docs.telecomtowerpower.com.br/" target="_blank" rel="noopener noreferrer">{t.nav.docs}</a>
            <a href="#faq">{t.nav.faq}</a>
            <button onClick={() => setLang(lang === "pt" ? "en" : "pt")} style={styles.langBtn}>
              {lang === "pt" ? "EN" : "PT"}
            </button>
            <button onClick={onLogin} style={styles.linkBtn}>{t.nav.login}</button>
            <LoginWithSSO returnTo="/portal" label="SSO" style={styles.linkBtn} />
            <button onClick={onSignup} style={styles.primaryBtn}>{t.nav.signup}</button>
          </nav>
        </div>
      </header>

      <section style={styles.hero}>
        <div style={styles.heroInner}>
          <div style={styles.eyebrow}>{t.hero.eyebrow}</div>
          <h1 style={styles.h1}>{t.hero.title}</h1>
          <p style={styles.sub}>{t.hero.sub}</p>
          <div style={styles.ctaRow}>
            <button onClick={onSignup} style={styles.primaryBtn}>{t.hero.cta1}</button>
            <a href="#pricing" style={styles.ghostBtn}>{t.hero.cta2}</a>
          </div>
          <div style={styles.badge}>{t.hero.badge}</div>
        </div>
      </section>

      <section style={styles.stats}>
        {t.stats.map((s, i) => (
          <div key={i} style={styles.stat}>
            <div style={styles.statN}>{s.n}</div>
            <div style={styles.statL}>{s.l}</div>
          </div>
        ))}
      </section>

      <section id="features" style={styles.section}>
        <h2 style={styles.h2}>{t.features.title}</h2>
        <div style={styles.grid3}>
          {t.features.items.map((f, i) => (
            <div key={i} style={styles.card}>
              <h3 style={styles.cardH}>{f.t}</h3>
              <p style={styles.cardP}>{f.d}</p>
            </div>
          ))}
        </div>
      </section>

      <section style={styles.sectionAlt}>
        <h2 style={styles.h2}>{t.useCases.title}</h2>
        <div style={styles.grid2}>
          {t.useCases.items.map((u, i) => (
            <div key={i} style={styles.card}>
              <h3 style={styles.cardH}>{u.t}</h3>
              <p style={styles.cardP}>{u.d}</p>
            </div>
          ))}
        </div>
      </section>

      <section style={styles.sectionAlt}>
        <RoiCalculator lang={lang} />
      </section>

      <section style={styles.section}>
        <SavingsLadder lang={lang} />
      </section>

      <section id="pricing" style={styles.section}>
        <Pricing lang={lang} onSignup={onSignup} />
      </section>

      <section id="faq" style={styles.sectionAlt}>
        <h2 style={styles.h2}>{t.faq.title}</h2>
        <div style={{ maxWidth: 760, margin: "0 auto" }}>
          {t.faq.items.map((f, i) => (
            <details key={i} style={styles.faq}>
              <summary style={styles.faqQ}>{f.q}</summary>
              <p style={styles.faqA}>{f.a}</p>
            </details>
          ))}
        </div>
      </section>

      <footer style={styles.footer}>
        <div style={styles.footerLinks}>
          <a href="mailto:support@telecomtowerpower.com.br">{t.footer.support}</a>
          <span style={styles.footerSep}>·</span>
          <a href="mailto:sales@telecomtowerpower.com.br">{t.footer.sales}</a>
          <span style={styles.footerSep}>·</span>
          <a href="https://monitoring.telecomtowerpower.com.br/" target="_blank" rel="noopener noreferrer">{t.footer.status}</a>
          <span style={styles.footerSep}>·</span>
          <a href={`https://docs.telecomtowerpower.com.br/${lang === "en" ? "en/" : ""}legal/terms/`}>{t.footer.terms}</a>
          <span style={styles.footerSep}>·</span>
          <a href={`https://docs.telecomtowerpower.com.br/${lang === "en" ? "en/" : ""}legal/privacy/`}>{t.footer.privacy}</a>
          <span style={styles.footerSep}>·</span>
          <a href={`https://docs.telecomtowerpower.com.br/${lang === "en" ? "en/" : ""}legal/refund-policy/`}>{t.footer.refund}</a>
          <span style={styles.footerSep}>·</span>
          <a href={`https://docs.telecomtowerpower.com.br/${lang === "en" ? "en/" : ""}legal/security/`}>{t.footer.security}</a>
          <span style={styles.footerSep}>·</span>
          <a href={`https://docs.telecomtowerpower.com.br/${lang === "en" ? "en/" : ""}legal/reliability/`}>{t.footer.reliability}</a>
          <span style={styles.footerSep}>·</span>
          <a href={`https://docs.telecomtowerpower.com.br/${lang === "en" ? "en/" : ""}contact/`}>{t.footer.contact}</a>
        </div>
        <div>{t.footer.tagline}</div>
      </footer>
    </div>
  );
}

const styles = {
  nav: { borderBottom: "1px solid #e2e8f0", position: "sticky", top: 0, background: "rgba(255,255,255,.9)", backdropFilter: "blur(8px)", zIndex: 10 },
  navInner: { maxWidth: 1120, margin: "0 auto", display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 24px" },
  brand: { fontWeight: 700, fontSize: 16 },
  navLinks: { display: "flex", alignItems: "center", gap: 20, fontSize: 14 },
  langBtn: { background: "transparent", border: "1px solid #cbd5e1", borderRadius: 6, padding: "4px 8px", cursor: "pointer", fontSize: 12 },
  linkBtn: { background: "transparent", border: "none", cursor: "pointer", color: "#0f172a", fontSize: 14 },
  primaryBtn: { background: "#0f172a", color: "#fff", border: "none", borderRadius: 8, padding: "10px 18px", cursor: "pointer", fontWeight: 600, fontSize: 14 },
  ghostBtn: { display: "inline-block", color: "#0f172a", textDecoration: "none", border: "1px solid #cbd5e1", borderRadius: 8, padding: "10px 18px", fontWeight: 600, fontSize: 14 },
  hero: { padding: "80px 24px", background: "linear-gradient(180deg, #f8fafc 0%, #fff 100%)" },
  heroInner: { maxWidth: 840, margin: "0 auto", textAlign: "center" },
  eyebrow: { color: "#0369a1", fontSize: 13, fontWeight: 600, letterSpacing: 0.5, textTransform: "uppercase", marginBottom: 16 },
  h1: { fontSize: 56, lineHeight: 1.1, fontWeight: 800, margin: "0 0 20px", letterSpacing: -1 },
  sub: { fontSize: 19, color: "#475569", lineHeight: 1.6, margin: "0 0 32px" },
  ctaRow: { display: "flex", gap: 12, justifyContent: "center", marginBottom: 24 },
  badge: { display: "inline-block", background: "#ecfccb", color: "#365314", padding: "6px 14px", borderRadius: 999, fontSize: 13 },
  stats: { maxWidth: 1120, margin: "0 auto", display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 24, padding: "40px 24px", borderTop: "1px solid #e2e8f0", borderBottom: "1px solid #e2e8f0" },
  stat: { textAlign: "center" },
  statN: { fontSize: 36, fontWeight: 800 },
  statL: { fontSize: 13, color: "#64748b", marginTop: 4 },
  section: { padding: "80px 24px", maxWidth: 1120, margin: "0 auto" },
  sectionAlt: { padding: "80px 24px", background: "#f8fafc" },
  h2: { fontSize: 36, fontWeight: 700, textAlign: "center", marginBottom: 48 },
  grid3: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 24 },
  grid2: { maxWidth: 960, margin: "0 auto", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 24 },
  card: { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, padding: 24 },
  cardH: { fontSize: 18, fontWeight: 700, margin: "0 0 8px" },
  cardP: { fontSize: 14, color: "#475569", lineHeight: 1.6, margin: 0 },
  faq: { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "16px 20px", marginBottom: 10 },
  faqQ: { fontWeight: 600, cursor: "pointer", fontSize: 15 },
  faqA: { color: "#475569", marginTop: 10, lineHeight: 1.6, fontSize: 14 },
  footer: { textAlign: "center", padding: "40px 24px", color: "#64748b", fontSize: 13, borderTop: "1px solid #e2e8f0" },
  footerLinks: { display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center", marginBottom: 16, fontSize: 13 },
  footerSep: { color: "#cbd5e1" }
};
