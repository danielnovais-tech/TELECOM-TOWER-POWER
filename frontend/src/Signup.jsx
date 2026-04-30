import { useState, useEffect, useRef } from "react";
import LoginWithSSO from "./LoginWithSSO";

const BASE = "/api";
const TURNSTILE_SCRIPT_ID = "cf-turnstile-script";

function loadTurnstileScript() {
  if (typeof window === "undefined") return;
  if (document.getElementById(TURNSTILE_SCRIPT_ID)) return;
  const s = document.createElement("script");
  s.id = TURNSTILE_SCRIPT_ID;
  s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
  s.async = true;
  s.defer = true;
  document.head.appendChild(s);
}

export default function Signup({ onKeyReceived }) {
  const [email, setEmail] = useState("");
  const [tier, setTier] = useState(() => {
    try {
      const preselected = window.sessionStorage.getItem("ttp_selected_tier");
      if (preselected && ["free", "starter", "pro", "business", "enterprise"].includes(preselected)) {
        return preselected;
      }
    } catch { /* sessionStorage unavailable */ }
    return "free";
  });
  const [billingCycle, setBillingCycle] = useState(() => {
    try {
      const c = window.sessionStorage.getItem("ttp_billing_cycle");
      return c === "annual" ? "annual" : "monthly";
    } catch { return "monthly"; }
  });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [captchaConfig, setCaptchaConfig] = useState({ required: false, site_key: null });
  const [captchaToken, setCaptchaToken] = useState("");
  const captchaContainerRef = useRef(null);
  const captchaWidgetIdRef = useRef(null);

  // Discover whether CAPTCHA is required and load Turnstile if so.
  useEffect(() => {
    let cancelled = false;
    fetch(`${BASE}/signup/config`)
      .then((r) => (r.ok ? r.json() : null))
      .then((cfg) => {
        if (cancelled || !cfg?.captcha) return;
        setCaptchaConfig(cfg.captcha);
        if (cfg.captcha.required && cfg.captcha.site_key) {
          loadTurnstileScript();
        }
      })
      .catch(() => { /* signup still works if config fetch fails */ });
    return () => { cancelled = true; };
  }, []);

  // Render the Turnstile widget once the script is loaded and we know the site key.
  useEffect(() => {
    if (!captchaConfig.required || !captchaConfig.site_key) return;
    let mounted = true;
    const tryRender = () => {
      if (!mounted) return;
      if (window.turnstile && captchaContainerRef.current && captchaWidgetIdRef.current === null) {
        captchaWidgetIdRef.current = window.turnstile.render(captchaContainerRef.current, {
          sitekey: captchaConfig.site_key,
          callback: (token) => setCaptchaToken(token || ""),
          "expired-callback": () => setCaptchaToken(""),
          "error-callback": () => setCaptchaToken(""),
        });
      } else {
        setTimeout(tryRender, 200);
      }
    };
    tryRender();
    return () => {
      mounted = false;
      if (window.turnstile && captchaWidgetIdRef.current !== null) {
        try { window.turnstile.remove(captchaWidgetIdRef.current); } catch { /* ignore */ }
        captchaWidgetIdRef.current = null;
      }
    };
  }, [captchaConfig]);

  // Clear the one-shot preselection once mounted so a later visit lands on Free.
  useEffect(() => {
    try {
      window.sessionStorage.removeItem("ttp_selected_tier");
      window.sessionStorage.removeItem("ttp_billing_cycle");
    } catch { /* ignore */ }
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setLoading(true);

    try {
      if (tier === "free") {
        if (captchaConfig.required && !captchaToken) {
          throw new Error("Please complete the CAPTCHA challenge.");
        }
        const r = await fetch(`${BASE}/signup/free`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, captcha_token: captchaToken || undefined }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || r.statusText);
        setResult(data);
        if (onKeyReceived) onKeyReceived(data.api_key);
      } else if (tier === "enterprise") {
        window.location.href = `mailto:sales@telecomtowerpower.com.br?subject=Enterprise%20plan%20inquiry&body=Email:%20${encodeURIComponent(email)}`;
      } else {
        const r = await fetch(`${BASE}/signup/checkout`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, tier, billing_cycle: billingCycle }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || r.statusText);
        window.location.href = data.checkout_url;
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="signup-page">
      <div className="signup-card">
        <h2>Get Your API Key</h2>
        <p className="signup-sub">Choose a plan and start building</p>

        <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "center", marginBottom: 16 }}>
          <LoginWithSSO
            returnTo="/portal"
            label="Sign in with company SSO"
            style={{
              padding: "10px 18px",
              borderRadius: 8,
              border: "1px solid #2563eb",
              background: "#fff",
              color: "#2563eb",
              fontWeight: 600,
              cursor: "pointer",
            }}
          />
        </div>

        <div style={{ display: "flex", justifyContent: "center", marginBottom: 16 }}>
          <div style={{ background: "#f1f5f9", padding: 4, borderRadius: 999, display: "inline-flex" }}>
            <button
              type="button"
              onClick={() => setBillingCycle("monthly")}
              style={{
                background: billingCycle === "monthly" ? "#fff" : "transparent",
                border: "none",
                padding: "6px 16px",
                borderRadius: 999,
                cursor: "pointer",
                fontWeight: 600,
                color: billingCycle === "monthly" ? "#0f172a" : "#64748b",
                boxShadow: billingCycle === "monthly" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              }}
            >
              Mensal
            </button>
            <button
              type="button"
              onClick={() => setBillingCycle("annual")}
              style={{
                background: billingCycle === "annual" ? "#fff" : "transparent",
                border: "none",
                padding: "6px 16px",
                borderRadius: 999,
                cursor: "pointer",
                fontWeight: 600,
                color: billingCycle === "annual" ? "#0f172a" : "#64748b",
                boxShadow: billingCycle === "annual" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              }}
            >
              Anual (−17%)
            </button>
          </div>
        </div>

        <div className="plan-grid">
          {[
            {
              id: "free",
              name: "Free",
              monthly: "R$ 0",
              annual: "R$ 0",
              bullets: ["200 chamadas/mês", "Até 20 torres/consulta", "5 PDFs/mês", "Sem IA"],
            },
            {
              id: "starter",
              name: "Starter",
              monthly: "R$ 79",
              annual: "R$ 65",
              bullets: ["3.000 chamadas/mês", "Até 100 torres/consulta", "50 PDFs/mês", "Lote até 100 receptores", "Suporte 48h"],
            },
            {
              id: "pro",
              name: "Pro",
              monthly: "R$ 349",
              annual: "R$ 289",
              bullets: ["25.000 chamadas/mês", "Até 500 torres/consulta", "500 PDFs/mês + IA", "Lote até 2.000 receptores", "Suporte 24h"],
              badge: "Mais popular",
            },
            {
              id: "business",
              name: "Business",
              monthly: "R$ 1.299",
              annual: "R$ 1.079",
              bullets: ["150.000 chamadas/mês", "Lote até 5.000 receptores", "5.000 PDFs/mês", "IA ilimitada", "Suporte 4h"],
            },
            {
              id: "enterprise",
              name: "Enterprise",
              monthly: "sob consulta",
              annual: "sob consulta",
              bullets: ["Volume customizado", "SLA 99.95%", "SSO SAML + IP allowlist", "Suporte 24/7"],
              custom: true,
            },
          ].map((p) => {
            const price = billingCycle === "annual" ? p.annual : p.monthly;
            const suffix = p.custom
              ? ""
              : billingCycle === "annual"
                ? "/mês, anual"
                : "/mês";
            return (
              <label
                key={p.id}
                className={`plan-option ${tier === p.id ? "selected" : ""}`}
                style={{ position: "relative" }}
              >
                <input
                  type="radio"
                  name="tier"
                  value={p.id}
                  checked={tier === p.id}
                  onChange={() => setTier(p.id)}
                />
                {p.badge && (
                  <span style={{
                    position: "absolute", top: -10, right: 12,
                    background: "#2563eb", color: "#fff",
                    fontSize: 11, fontWeight: 600,
                    padding: "2px 8px", borderRadius: 999,
                  }}>{p.badge}</span>
                )}
                <div className="plan-header">{p.name}</div>
                <div className="plan-price">
                  {price}
                  {suffix && <span>{suffix}</span>}
                </div>
                <ul>
                  {p.bullets.map((b) => <li key={b}>{b}</li>)}
                </ul>
              </label>
            );
          })}
        </div>

        <form onSubmit={handleSubmit} className="signup-form">
          <input
            type="email"
            required
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          {tier === "free" && captchaConfig.required && (
            <div
              ref={captchaContainerRef}
              className="cf-turnstile"
              style={{ margin: "8px 0", display: "flex", justifyContent: "center" }}
            />
          )}
          <button type="submit" className="btn primary" disabled={loading}>
            {loading
              ? "Processando…"
              : tier === "free"
                ? "Criar conta Free"
                : tier === "enterprise"
                  ? "Falar com vendas"
                  : `Assinar ${tier.charAt(0).toUpperCase() + tier.slice(1)}`}
          </button>
          <p style={{ fontSize: 12, color: "#64748b", textAlign: "center", marginTop: 12, lineHeight: 1.5 }}>
            Ao continuar, você concorda com os{" "}
            <a
              href="https://docs.telecomtowerpower.com.br/legal/terms/"
              target="_blank"
              rel="noopener noreferrer"
            >
              Termos de Serviço
            </a>{" "}
            e a{" "}
            <a
              href="https://docs.telecomtowerpower.com.br/legal/privacy/"
              target="_blank"
              rel="noopener noreferrer"
            >
              Política de Privacidade
            </a>
            .
          </p>
        </form>

        {error && <div className="signup-error">{error}</div>}

        {result && (
          <div className="signup-success">
            <p>Account created! Your API key:</p>
            <code className="api-key-display">{result.api_key}</code>
            <p className="key-warning">Save this key — it won't be shown again.</p>
          </div>
        )}
      </div>
    </div>
  );
}
