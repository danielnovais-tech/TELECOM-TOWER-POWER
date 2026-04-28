import { useEffect, useState, useCallback } from "react";
import TowerMap from "./TowerMap";
import Sidebar from "./Sidebar";
import Signup from "./Signup";
import SignupSuccess from "./SignupSuccess";
import BedrockPlayground from "./BedrockPlayground";
import Portal from "./Portal";
import Landing from "./Landing";
import Pricing from "./Pricing";
import AuthCallback from "./AuthCallback";
import SalesDashboard from "./admin/SalesDashboard";
import { fetchTowers, fetchHealth, setApiKey, onRateLimitChange } from "./api";
import "./App.css";

function getInitialPage() {
  const path = window.location.pathname;
  if (path === "/auth/callback") return "auth-callback";
  if (path === "/signup/success") return "signup-success";
  if (path === "/signup/cancel") return "signup-cancel";
  if (path === "/app" || path === "/map") return "map";
  if (path === "/pricing") return "pricing";
  if (path === "/signup") return "signup";
  if (path === "/portal" || path === "/account") return "portal";
  if (path === "/admin/sales" || path === "/admin") return "admin-sales";
  if (path === "/") return "landing";
  return "landing";
}

function getSessionId() {
  return new URLSearchParams(window.location.search).get("session_id");
}

export default function App() {
  const [page, setPage] = useState(getInitialPage);
  const [towers, setTowers] = useState([]);
  const [selectedTower, setSelectedTower] = useState(null);
  const [receiverPos, setReceiverPos] = useState(null);
  const [analysisResult, setAnalysisResult] = useState(null);
  const [repeaterChain, setRepeaterChain] = useState([]);
  const [healthStatus, setHealthStatus] = useState(null);
  const [rateLimitInfo, setRateLimitInfo] = useState({ remaining: null, limit: null });
  const [rateLimitToast, setRateLimitToast] = useState(null);
  const [isOffline, setIsOffline] = useState(!navigator.onLine);

  useEffect(() => {
    const goOffline = () => setIsOffline(true);
    const goOnline = () => setIsOffline(false);
    window.addEventListener("offline", goOffline);
    window.addEventListener("online", goOnline);
    return () => {
      window.removeEventListener("offline", goOffline);
      window.removeEventListener("online", goOnline);
    };
  }, []);

  useEffect(() => {
    fetchTowers().then(setTowers).catch(console.error);
    fetchHealth().then(setHealthStatus).catch(console.error);
  }, []);

  // Subscribe to rate-limit header updates
  useEffect(() => {
    return onRateLimitChange((info) => {
      setRateLimitInfo(info);
      if (info.remaining != null && info.limit != null && info.remaining <= 2 && info.remaining > 0) {
        setRateLimitToast({
          type: "warning",
          message: `${info.remaining} request${info.remaining === 1 ? "" : "s"} remaining (${info.limit}/min limit).`,
        });
      }
    });
  }, []);

  // Auto-dismiss toast after 6 seconds
  useEffect(() => {
    if (!rateLimitToast) return;
    const t = setTimeout(() => setRateLimitToast(null), 6000);
    return () => clearTimeout(t);
  }, [rateLimitToast]);

  const handleMapClick = useCallback((latlng) => {
    setReceiverPos(latlng);
    setAnalysisResult(null);
    setRepeaterChain([]);
  }, []);

  const handleTowerSelect = useCallback((tower) => {
    setSelectedTower(tower);
    setAnalysisResult(null);
    setRepeaterChain([]);
  }, []);

  return (
    <div className="app-layout">
      {page === "auth-callback" ? (
        <AuthCallback />
      ) : page === "admin-sales" ? (
        <SalesDashboard />
      ) : page === "landing" ? (
        <Landing
          onSignup={(tierId, cycle) => {
            window.history.pushState({}, "", "/signup");
            setPage("signup");
            if (tierId) window.sessionStorage.setItem("ttp_selected_tier", tierId);
            if (cycle) window.sessionStorage.setItem("ttp_billing_cycle", cycle);
          }}
          onLogin={() => { window.history.pushState({}, "", "/portal"); setPage("portal"); }}
        />
      ) : page === "pricing" ? (
        <div style={{ padding: "60px 24px", maxWidth: 1120, margin: "0 auto" }}>
          <Pricing
            lang={(navigator.language || "pt").toLowerCase().startsWith("pt") ? "pt" : "en"}
            onSignup={(tierId, cycle) => {
              window.history.pushState({}, "", "/signup");
              setPage("signup");
              if (tierId) window.sessionStorage.setItem("ttp_selected_tier", tierId);
              if (cycle) window.sessionStorage.setItem("ttp_billing_cycle", cycle);
            }}
          />
        </div>
      ) : (
      <>
      <header className="app-header">
        <h1>Telecom Tower Power</h1>
        <span className="subtitle">RF Link Analysis &amp; Repeater Planner</span>
        <nav className="header-nav">
          <button onClick={() => { window.history.pushState({}, "", "/"); setPage("landing"); }}>
            Home
          </button>
          <button className={page === "map" ? "active" : ""} onClick={() => { window.history.pushState({}, "", "/app"); setPage("map"); }}>
            Map
          </button>
          <button className={page === "ai" ? "active" : ""} onClick={() => { window.history.pushState({}, "", "/app"); setPage("ai"); }}>
            AI Playground
          </button>
          <button className={page === "pricing" ? "active" : ""} onClick={() => { window.history.pushState({}, "", "/pricing"); setPage("pricing"); }}>
            Pricing
          </button>
          <button className={page === "signup" ? "active" : ""} onClick={() => { window.history.pushState({}, "", "/signup"); setPage("signup"); }}>
            Get API Key
          </button>
          <button className={page === "portal" ? "active" : ""} onClick={() => { window.history.pushState({}, "", "/portal"); setPage("portal"); }}>
            My Account
          </button>
        </nav>
      </header>

      {page === "map" ? (
        <div className="app-body">
          <Sidebar
            towers={towers}
            selectedTower={selectedTower}
            receiverPos={receiverPos}
            analysisResult={analysisResult}
            setAnalysisResult={setAnalysisResult}
            repeaterChain={repeaterChain}
            setRepeaterChain={setRepeaterChain}
            healthStatus={healthStatus}
            rateLimitInfo={rateLimitInfo}
            onNavigateSignup={() => { window.history.pushState({}, "", "/"); setPage("signup"); }}
          />
          <main className="map-container">
            <TowerMap
              towers={towers}
              receiverPos={receiverPos}
              onMapClick={handleMapClick}
              onTowerSelect={handleTowerSelect}
              selectedTower={selectedTower}
              analysisResult={analysisResult}
              repeaterChain={repeaterChain}
            />
          </main>
        </div>
      ) : page === "signup-success" ? (
        <SignupSuccess
          sessionId={getSessionId()}
          onKeyReceived={(key) => { setApiKey(key); setPage("map"); }}
        />
      ) : page === "ai" ? (
        <BedrockPlayground
          analysisResult={analysisResult}
          selectedTower={selectedTower}
        />
      ) : page === "signup-cancel" ? (
        <div className="signup-page">
          <div className="signup-card">
            <h2>Checkout Cancelled</h2>
            <p className="signup-sub">Your payment was not processed. No charges were made.</p>
            <button
              className="btn primary"
              onClick={() => { window.history.pushState({}, "", "/"); setPage("signup"); }}
            >
              Try Again
            </button>
          </div>
        </div>
      ) : page === "portal" ? (
        <Portal />
      ) : (
        <Signup onKeyReceived={(key) => { setApiKey(key); setPage("map"); }} />
      )}
      </>
      )}

      {rateLimitToast && (
        <div className={`rate-limit-toast ${rateLimitToast.type}`}>
          <span>{rateLimitToast.message}</span>
          <button className="toast-close" onClick={() => setRateLimitToast(null)}>×</button>
        </div>
      )}

      {isOffline && (
        <div className="offline-banner">
          You are offline — showing cached data
        </div>
      )}
    </div>
  );
}
