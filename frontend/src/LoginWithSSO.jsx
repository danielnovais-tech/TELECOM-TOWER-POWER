import { useEffect, useState } from "react";
import { fetchSsoConfig, buildAuthorizeUrl } from "./auth";

/**
 * "Login with SSO" button. Hides itself when /auth/sso/config returns
 * enabled:false or when hosted_ui is missing on the server response.
 *
 * @param {{ returnTo?: string, label?: string, style?: React.CSSProperties, className?: string }} props
 */
export default function LoginWithSSO({
  returnTo = "/portal",
  label = "Sign in with SSO",
  style,
  className,
}) {
  const [cfg, setCfg] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchSsoConfig().then((c) => {
      if (!cancelled) setCfg(c);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!cfg || !cfg.hosted_ui) return null;

  const onClick = () => {
    setLoading(true);
    window.location.href = buildAuthorizeUrl(cfg.hosted_ui, returnTo);
  };

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className={className}
      style={style}
    >
      {loading ? "Redirecting…" : label}
    </button>
  );
}
