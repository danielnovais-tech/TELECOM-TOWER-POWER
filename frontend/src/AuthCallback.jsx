import { useEffect, useRef, useState } from "react";
import {
  consumeReturnTo,
  exchangeCodeForApiKey,
  verifyAndConsumeState,
} from "./auth";
import { setApiKey } from "./api";

/**
 * /auth/callback handler. Reads ?code=&state= from the URL, validates
 * the CSRF state, posts the code to the backend for exchange, stores
 * the resulting api_key, and navigates to the saved returnTo path.
 *
 * @param {{ onSuccess?: (info: { api_key: string, tier?: string, email?: string }) => void }} props
 */
export default function AuthCallback({ onSuccess }) {
  const [status, setStatus] = useState("Signing you in…");
  const [error, setError] = useState(null);
  const ranRef = useRef(false);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;

    const url = new URL(window.location.href);
    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state");
    const oauthError = url.searchParams.get("error");

    if (oauthError) {
      const desc = url.searchParams.get("error_description") || oauthError;
      setError(`Sign-in cancelled: ${desc}`);
      return;
    }
    if (!code) {
      setError("Missing authorization code from identity provider.");
      return;
    }
    if (!verifyAndConsumeState(state)) {
      setError("Sign-in failed: state mismatch (possible CSRF). Please try again.");
      return;
    }

    exchangeCodeForApiKey(code)
      .then((info) => {
        setApiKey(info.api_key);
        setStatus("Signed in. Redirecting…");
        const returnTo = consumeReturnTo();
        if (typeof onSuccess === "function") {
          onSuccess(info);
        }
        // Clean the OAuth params from the URL before navigating away.
        window.history.replaceState({}, "", returnTo);
        // Force a full reload so all components pick up the new api key
        // (api.js initialises its client at module load).
        window.location.reload();
      })
      .catch((err) => {
        setError(err && err.message ? err.message : "Sign-in failed.");
      });
  }, [onSuccess]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "60vh",
        gap: "1rem",
        padding: "2rem",
        textAlign: "center",
      }}
    >
      {error ? (
        <>
          <h2 style={{ margin: 0, color: "#b91c1c" }}>Sign-in failed</h2>
          <p style={{ maxWidth: 480, color: "#374151" }}>{error}</p>
          <a href="/" style={{ color: "#2563eb" }}>
            Return home
          </a>
        </>
      ) : (
        <>
          <div
            style={{
              width: 32,
              height: 32,
              border: "3px solid #e5e7eb",
              borderTopColor: "#2563eb",
              borderRadius: "50%",
              animation: "spin 0.8s linear infinite",
            }}
          />
          <p style={{ color: "#374151" }}>{status}</p>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </>
      )}
    </div>
  );
}
