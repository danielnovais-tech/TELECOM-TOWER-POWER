import { useEffect, useState, useCallback } from "react";
import TowerMap from "./TowerMap";
import Sidebar from "./Sidebar";
import Signup from "./Signup";
import SignupSuccess from "./SignupSuccess";
import BedrockPlayground from "./BedrockPlayground";
import { fetchTowers, fetchHealth, setApiKey, onRateLimitChange } from "./api";
import "./App.css";

function getInitialPage() {
  const path = window.location.pathname;
  if (path === "/signup/success") return "signup-success";
  if (path === "/signup/cancel") return "signup-cancel";
  return "map";
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
      <header className="app-header">
        <h1>Telecom Tower Power</h1>
        <span className="subtitle">RF Link Analysis &amp; Repeater Planner</span>
        <nav className="header-nav">
          <button className={page === "map" ? "active" : ""} onClick={() => { window.history.pushState({}, "", "/"); setPage("map"); }}>
            Map
          </button>
          <button className={page === "ai" ? "active" : ""} onClick={() => { window.history.pushState({}, "", "/"); setPage("ai"); }}>
            AI Playground
          </button>
          <button className={page === "signup" ? "active" : ""} onClick={() => { window.history.pushState({}, "", "/"); setPage("signup"); }}>
            Get API Key
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
      ) : (
        <Signup onKeyReceived={(key) => { setApiKey(key); setPage("map"); }} />
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
