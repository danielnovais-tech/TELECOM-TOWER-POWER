import { useEffect, useState } from "react";

const BASE = "/api";

export default function SignupSuccess({ sessionId, onKeyReceived }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [retries, setRetries] = useState(0);

  useEffect(() => {
    if (!sessionId) {
      setError("No session ID found in the URL.");
      setLoading(false);
      return;
    }

    let cancelled = false;
    const maxRetries = 10;
    const delay = 2000;

    async function poll() {
      for (let i = 0; i <= maxRetries; i++) {
        if (cancelled) return;
        try {
          const r = await fetch(
            `${BASE}/signup/success?session_id=${encodeURIComponent(sessionId)}`
          );
          const data = await r.json();
          if (r.ok) {
            if (!cancelled) {
              setResult(data);
              setLoading(false);
              if (onKeyReceived) onKeyReceived(data.api_key);
            }
            return;
          }
          if (r.status === 404 && i < maxRetries) {
            setRetries(i + 1);
            await new Promise((res) => setTimeout(res, delay));
            continue;
          }
          throw new Error(data.detail || r.statusText);
        } catch (err) {
          if (!cancelled) {
            setError(err.message);
            setLoading(false);
          }
          return;
        }
      }
      if (!cancelled) {
        setError(
          "Your payment was received but the API key is still being provisioned. " +
            "Please check back shortly or contact support."
        );
        setLoading(false);
      }
    }

    poll();
    return () => {
      cancelled = true;
    };
  }, [sessionId, onKeyReceived]);

  return (
    <div className="signup-page">
      <div className="signup-card">
        {loading && (
          <>
            <h2>Confirming Payment…</h2>
            <p className="signup-sub">
              Verifying your payment with Stripe
              {retries > 0 && ` (attempt ${retries + 1})`}…
            </p>
            <div className="spinner" />
          </>
        )}

        {error && (
          <>
            <h2>Something Went Wrong</h2>
            <div className="signup-error">{error}</div>
          </>
        )}

        {result && (
          <>
            <h2>Payment Confirmed!</h2>
            <p className="signup-sub">
              Your <strong>{result.tier}</strong> account is ready.
            </p>
            <div className="signup-success">
              <p>Your API key:</p>
              <code className="api-key-display">{result.api_key}</code>
              <p className="key-warning">
                Save this key — it won't be shown again.
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
