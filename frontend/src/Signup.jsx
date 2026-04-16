import { useState } from "react";

const BASE = "/api";

export default function Signup({ onKeyReceived }) {
  const [email, setEmail] = useState("");
  const [tier, setTier] = useState("free");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setLoading(true);

    try {
      if (tier === "free") {
        const r = await fetch(`${BASE}/signup/free`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || r.statusText);
        setResult(data);
        if (onKeyReceived) onKeyReceived(data.api_key);
      } else {
        const r = await fetch(`${BASE}/signup/checkout`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, tier }),
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

        <div className="plan-grid">
          <label className={`plan-option ${tier === "free" ? "selected" : ""}`}>
            <input type="radio" name="tier" value="free"
              checked={tier === "free"} onChange={() => setTier("free")} />
            <div className="plan-header">Free</div>
            <div className="plan-price">$0<span>/mo</span></div>
            <ul>
              <li>10 requests / min</li>
              <li>20 towers</li>
              <li>Link analysis</li>
            </ul>
          </label>

          <label className={`plan-option ${tier === "pro" ? "selected" : ""}`}>
            <input type="radio" name="tier" value="pro"
              checked={tier === "pro"} onChange={() => setTier("pro")} />
            <div className="plan-header">Pro</div>
            <div className="plan-price">R$ 1.000<span>/mo</span></div>
            <ul>
              <li>100 requests / min</li>
              <li>500 towers</li>
              <li>2,000 batch rows</li>
              <li>PDF reports</li>
              <li>AI assistant</li>
            </ul>
          </label>

          <label className={`plan-option ${tier === "enterprise" ? "selected" : ""}`}>
            <input type="radio" name="tier" value="enterprise"
              checked={tier === "enterprise"} onChange={() => setTier("enterprise")} />
            <div className="plan-header">Enterprise</div>
            <div className="plan-price">R$ 5.000<span>/mo</span></div>
            <ul>
              <li>1,000 requests / min</li>
              <li>10,000 towers</li>
              <li>10,000 batch rows</li>
              <li>PDF reports</li>
              <li>AI assistant</li>
            </ul>
          </label>
        </div>

        <form onSubmit={handleSubmit} className="signup-form">
          <input
            type="email"
            required
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <button type="submit" className="btn primary" disabled={loading}>
            {loading
              ? "Processing…"
              : tier === "free"
                ? "Create Free Account"
                : `Subscribe to ${tier.charAt(0).toUpperCase() + tier.slice(1)}`}
          </button>
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
