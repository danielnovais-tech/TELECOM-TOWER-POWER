/**
 * SSO (Cognito Hosted UI) helpers for the SPA.
 *
 * Flow:
 *   1. User clicks "Login with SSO" → buildAuthorizeUrl() → redirect.
 *   2. Cognito redirects back to /auth/callback?code=...&state=...
 *   3. AuthCallback.jsx posts {code, redirect_uri} to /auth/sso/callback
 *      on the backend, which exchanges with Cognito (using client_secret),
 *      verifies the id_token, provisions/returns the api_key.
 *   4. setApiKey(api_key) → SPA continues with X-API-Key as before.
 *
 * The client_secret never reaches the browser. The state param is a
 * CSRF token stored in sessionStorage and validated on callback.
 */

const API_BASE =
  window.__RUNTIME_CONFIG__?.API_BASE ||
  import.meta.env.VITE_API_BASE ||
  "/api";

const STATE_KEY = "sso_oauth_state";
const RETURN_KEY = "sso_return_to";

function randomState() {
  const buf = new Uint8Array(16);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Fetch the public SSO config (idP, hosted-UI URLs). Returns null when
 * SSO is not configured server-side.
 * @returns {Promise<null | {
 *   enabled: boolean,
 *   provider?: string,
 *   issuer?: string,
 *   audience?: string,
 *   hosted_ui?: { authorize_url: string, logout_url: string, client_id: string, scope: string }
 * }>}
 */
export async function fetchSsoConfig() {
  try {
    const res = await fetch(`${API_BASE}/auth/sso/config`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data?.enabled ? data : null;
  } catch {
    return null;
  }
}

/** Returns the absolute redirect_uri this SPA registers with Cognito. */
export function getRedirectUri() {
  return `${window.location.origin}/auth/callback`;
}

/**
 * Build the Cognito Hosted UI authorize URL. Stores a CSRF state token
 * in sessionStorage so the callback can verify it.
 * @param {{ authorize_url: string, client_id: string, scope: string }} cfg
 * @param {string} [returnTo] path to navigate to after a successful login.
 */
export function buildAuthorizeUrl(cfg, returnTo = "/portal") {
  const state = randomState();
  sessionStorage.setItem(STATE_KEY, state);
  sessionStorage.setItem(RETURN_KEY, returnTo);
  const params = new URLSearchParams({
    client_id: cfg.client_id,
    response_type: "code",
    scope: cfg.scope || "openid email profile",
    redirect_uri: getRedirectUri(),
    state,
  });
  return `${cfg.authorize_url}?${params.toString()}`;
}

/** Validate the state param echoed back by Cognito. */
export function verifyAndConsumeState(state) {
  const expected = sessionStorage.getItem(STATE_KEY);
  sessionStorage.removeItem(STATE_KEY);
  return Boolean(expected) && state === expected;
}

/** Pop the post-login destination saved by buildAuthorizeUrl(). */
export function consumeReturnTo() {
  const v = sessionStorage.getItem(RETURN_KEY) || "/portal";
  sessionStorage.removeItem(RETURN_KEY);
  return v;
}

/**
 * Server-side OAuth code → api_key exchange.
 * @param {string} code
 * @returns {Promise<{ api_key: string, tier: string, email: string, sso_enabled: boolean, created: boolean }>}
 */
export async function exchangeCodeForApiKey(code) {
  const res = await fetch(`${API_BASE}/auth/sso/callback`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({
      code,
      redirect_uri: getRedirectUri(),
      provider: "cognito",
    }),
  });
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    /* keep null */
  }
  if (!res.ok) {
    const detail = (body && body.detail) || `HTTP ${res.status}`;
    throw new Error(`SSO exchange failed: ${detail}`);
  }
  if (!body || typeof body.api_key !== "string") {
    throw new Error("SSO exchange returned no api_key");
  }
  return body;
}
