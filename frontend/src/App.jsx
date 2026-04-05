import { useEffect, useState, useCallback } from "react";
import TowerMap from "./TowerMap";
import Sidebar from "./Sidebar";
import Signup from "./Signup";
import { fetchTowers, fetchHealth } from "./api";
import "./App.css";

export default function App() {
  const [page, setPage] = useState("map"); // "map" | "signup"
  const [towers, setTowers] = useState([]);
  const [selectedTower, setSelectedTower] = useState(null);
  const [receiverPos, setReceiverPos] = useState(null);
  const [analysisResult, setAnalysisResult] = useState(null);
  const [repeaterChain, setRepeaterChain] = useState([]);
  const [healthStatus, setHealthStatus] = useState(null);

  useEffect(() => {
    fetchTowers().then(setTowers).catch(console.error);
    fetchHealth().then(setHealthStatus).catch(console.error);
  }, []);

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
          <button className={page === "map" ? "active" : ""} onClick={() => setPage("map")}>
            Map
          </button>
          <button className={page === "signup" ? "active" : ""} onClick={() => setPage("signup")}>
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
      ) : (
        <Signup onKeyReceived={(key) => console.log("New API key:", key)} />
      )}
    </div>
  );
}
