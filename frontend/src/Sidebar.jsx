import { useState } from "react";
import { analyzeLink, planRepeater, downloadPdfReport, RateLimitError } from "./api";

export default function Sidebar({
  towers,
  selectedTower,
  receiverPos,
  analysisResult,
  setAnalysisResult,
  repeaterChain,
  setRepeaterChain,
  healthStatus,
  rateLimitInfo,
  onNavigateSignup,
}) {
  const [rxHeight, setRxHeight] = useState(10);
  const [rxGain, setRxGain] = useState(12);
  const [maxHops, setMaxHops] = useState(3);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const canAnalyze = selectedTower && receiverPos;

  async function handleAnalyze() {
    if (!canAnalyze) return;
    setLoading(true);
    setError(null);
    setRepeaterChain([]);
    try {
      const result = await analyzeLink(selectedTower.id, {
        lat: receiverPos.lat,
        lon: receiverPos.lng,
        height_m: rxHeight,
        antenna_gain_dbi: rxGain,
      });
      setAnalysisResult(result);
    } catch (e) {
      if (e instanceof RateLimitError) {
        setError("rate-limit");
      } else {
        setError(e.message);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleRepeater() {
    if (!canAnalyze) return;
    setLoading(true);
    setError(null);
    try {
      const result = await planRepeater(
        selectedTower.id,
        { lat: receiverPos.lat, lon: receiverPos.lng, height_m: rxHeight, antenna_gain_dbi: rxGain },
        maxHops
      );
      setRepeaterChain(result.repeater_chain || []);
    } catch (e) {
      if (e instanceof RateLimitError) {
        setError("rate-limit");
      } else {
        setError(e.message);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleDownloadPdf() {
    if (!canAnalyze) return;
    setLoading(true);
    setError(null);
    try {
      const blobUrl = await downloadPdfReport(
        selectedTower.id, receiverPos.lat, receiverPos.lng, rxHeight, rxGain
      );
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = `report_${selectedTower.id}.pdf`;
      a.click();
      URL.revokeObjectURL(blobUrl);
    } catch (e) {
      if (e instanceof RateLimitError) {
        setError("rate-limit");
      } else {
        setError(e.message);
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="sidebar">
      {/* health */}
      <section className="panel">
        <h3>System</h3>
        {healthStatus ? (
          <div className="health-ok">
            <span className="dot green" /> Healthy &middot; {healthStatus.towers_loaded} towers
          </div>
        ) : (
          <div className="health-err"><span className="dot red" /> Connecting&hellip;</div>
        )}
      </section>

      {/* instructions */}
      <section className="panel">
        <h3>How to use</h3>
        <ol className="instructions">
          <li>Click a <span style={{ color: "#dc2626" }}>red tower</span> marker to select it</li>
          <li>Click anywhere on the map to place a <span style={{ color: "#2563eb" }}>blue receiver</span></li>
          <li>Press <b>Analyze Link</b> or <b>Plan Repeaters</b></li>
        </ol>
      </section>

      {/* selected tower */}
      <section className="panel">
        <h3>Selected Tower</h3>
        {selectedTower ? (
          <table className="info-table">
            <tbody>
              <tr><td>ID</td><td>{selectedTower.id}</td></tr>
              <tr><td>Operator</td><td>{selectedTower.operator}</td></tr>
              <tr><td>Height</td><td>{selectedTower.height_m} m</td></tr>
              <tr><td>Power</td><td>{selectedTower.power_dbm} dBm</td></tr>
              <tr><td>Bands</td><td>{(selectedTower.bands || []).join(", ")}</td></tr>
            </tbody>
          </table>
        ) : (
          <p className="muted">Click a tower on the map</p>
        )}
      </section>

      {/* receiver params */}
      <section className="panel">
        <h3>Receiver</h3>
        {receiverPos ? (
          <p className="coords">
            {receiverPos.lat.toFixed(5)}, {receiverPos.lng.toFixed(5)}
          </p>
        ) : (
          <p className="muted">Click the map to place receiver</p>
        )}
        <div className="field">
          <label>Height (m)</label>
          <input type="number" value={rxHeight} min={1} max={200}
            onChange={(e) => setRxHeight(Number(e.target.value))} />
        </div>
        <div className="field">
          <label>Antenna Gain (dBi)</label>
          <input type="number" value={rxGain} min={0} max={30} step={0.5}
            onChange={(e) => setRxGain(Number(e.target.value))} />
        </div>
        <div className="field">
          <label>Max Hops</label>
          <input type="number" value={maxHops} min={1} max={10}
            onChange={(e) => setMaxHops(Number(e.target.value))} />
        </div>
      </section>

      {/* actions */}
      <section className="panel actions">
        <button className="btn primary" disabled={!canAnalyze || loading} onClick={handleAnalyze}>
          {loading ? "Analyzing…" : "Analyze Link"}
        </button>
        <button className="btn secondary" disabled={!canAnalyze || loading} onClick={handleRepeater}>
          Plan Repeaters
        </button>
        <button className="btn outline" disabled={!canAnalyze} onClick={handleDownloadPdf}>
          Download PDF
        </button>
      </section>

      {/* error */}
      {error && (
        <section className="panel error-box">
          {error === "rate-limit" ? (
            <div className="rate-limit-error">
              <strong>Rate limit exceeded.</strong>
              <p>
                You&apos;ve hit the {rateLimitInfo?.limit ?? "–"} requests/min limit for your plan.
                {" "}
                <button className="link-btn" onClick={onNavigateSignup}>
                  Upgrade to Pro
                </button>
                {" "}for 100 req/min.
              </p>
            </div>
          ) : (
            error
          )}
        </section>
      )}

      {/* rate limit indicator */}
      {rateLimitInfo.remaining != null && rateLimitInfo.limit != null && (
        <section className="panel rate-limit-bar">
          <h3>API Rate Limit</h3>
          <div className="rl-track">
            <div
              className={`rl-fill${rateLimitInfo.remaining <= 2 ? " rl-low" : ""}`}
              style={{ width: `${(rateLimitInfo.remaining / rateLimitInfo.limit) * 100}%` }}
            />
          </div>
          <span className="rl-label">
            {rateLimitInfo.remaining} / {rateLimitInfo.limit} remaining
          </span>
        </section>
      )}

      {/* analysis result */}
      {analysisResult && (
        <section className="panel result">
          <h3>Link Analysis</h3>
          <table className="info-table">
            <tbody>
              <tr>
                <td>Feasible</td>
                <td className={analysisResult.feasible ? "text-green" : "text-red"}>
                  {analysisResult.feasible ? "Yes" : "No"}
                </td>
              </tr>
              <tr><td>Signal</td><td>{analysisResult.signal_dbm?.toFixed(1)} dBm</td></tr>
              <tr><td>Distance</td><td>{analysisResult.distance_km?.toFixed(2)} km</td></tr>
              <tr><td>Fresnel</td><td>{(analysisResult.fresnel_clearance * 100)?.toFixed(0)}%</td></tr>
              <tr><td>LOS</td><td>{analysisResult.los_ok ? "Clear" : "Obstructed"}</td></tr>
            </tbody>
          </table>
          <p className="recommendation">{analysisResult.recommendation}</p>
        </section>
      )}

      {/* repeater chain */}
      {repeaterChain.length > 0 && (
        <section className="panel result">
          <h3>Repeater Chain ({repeaterChain.length} hops)</h3>
          <ol className="chain-list">
            {repeaterChain.map((r, i) => (
              <li key={i}>{r.id} — {r.operator} ({r.height_m}m)</li>
            ))}
          </ol>
        </section>
      )}

      {/* tower list */}
      <section className="panel">
        <h3>All Towers ({towers.length})</h3>
        <ul className="tower-list">
          {towers.map((t) => (
            <li key={t.id} className={selectedTower?.id === t.id ? "active" : ""}>
              <strong>{t.id}</strong> — {t.operator}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
