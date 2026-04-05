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
import { useEffect, useState, useCallback } from "react";
import TowerMap from "./TowerMap";
import Sidebar from "./Sidebar";
import { fetchTowers, fetchHealth } from "./api";
import "./App.css";

export default function App() {
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
      </header>
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
    </div>
  );
}
import { useState } from 'react'
import reactLogo from './assets/react.svg'
import viteLogo from './assets/vite.svg'
import heroImg from './assets/hero.png'
import './App.css'

function App() {
  const [count, setCount] = useState(0)

  return (
    <>
      <section id="center">
        <div className="hero">
          <img src={heroImg} className="base" width="170" height="179" alt="" />
          <img src={reactLogo} className="framework" alt="React logo" />
          <img src={viteLogo} className="vite" alt="Vite logo" />
        </div>
        <div>
          <h1>Get started</h1>
          <p>
            Edit <code>src/App.jsx</code> and save to test <code>HMR</code>
          </p>
        </div>
        <button
          className="counter"
          onClick={() => setCount((count) => count + 1)}
        >
          Count is {count}
        </button>
      </section>

      <div className="ticks"></div>

      <section id="next-steps">
        <div id="docs">
          <svg className="icon" role="presentation" aria-hidden="true">
            <use href="/icons.svg#documentation-icon"></use>
          </svg>
          <h2>Documentation</h2>
          <p>Your questions, answered</p>
          <ul>
            <li>
              <a href="https://vite.dev/" target="_blank">
                <img className="logo" src={viteLogo} alt="" />
                Explore Vite
              </a>
            </li>
            <li>
              <a href="https://react.dev/" target="_blank">
                <img className="button-icon" src={reactLogo} alt="" />
                Learn more
              </a>
            </li>
          </ul>
        </div>
        <div id="social">
          <svg className="icon" role="presentation" aria-hidden="true">
            <use href="/icons.svg#social-icon"></use>
          </svg>
          <h2>Connect with us</h2>
          <p>Join the Vite community</p>
          <ul>
            <li>
              <a href="https://github.com/vitejs/vite" target="_blank">
                <svg
                  className="button-icon"
                  role="presentation"
                  aria-hidden="true"
                >
                  <use href="/icons.svg#github-icon"></use>
                </svg>
                GitHub
              </a>
            </li>
            <li>
              <a href="https://chat.vite.dev/" target="_blank">
                <svg
                  className="button-icon"
                  role="presentation"
                  aria-hidden="true"
                >
                  <use href="/icons.svg#discord-icon"></use>
                </svg>
                Discord
              </a>
            </li>
            <li>
              <a href="https://x.com/vite_js" target="_blank">
                <svg
                  className="button-icon"
                  role="presentation"
                  aria-hidden="true"
                >
                  <use href="/icons.svg#x-icon"></use>
                </svg>
                X.com
              </a>
            </li>
            <li>
              <a href="https://bsky.app/profile/vite.dev" target="_blank">
                <svg
                  className="button-icon"
                  role="presentation"
                  aria-hidden="true"
                >
                  <use href="/icons.svg#bluesky-icon"></use>
                </svg>
                Bluesky
              </a>
            </li>
          </ul>
        </div>
      </section>

      <div className="ticks"></div>
      <section id="spacer"></section>
    </>
  )
}

export default App
