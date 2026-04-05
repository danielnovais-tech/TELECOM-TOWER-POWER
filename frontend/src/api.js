const BASE = "/api";
const API_KEY = "demo-key-pro-001";

const headers = (extra = {}) => ({
  "X-API-Key": API_KEY,
  "Content-Type": "application/json",
  ...extra,
});

export async function fetchHealth() {
  const r = await fetch(`${BASE}/health`);
  return r.json();
}

export async function fetchTowers(operator = null) {
  const params = new URLSearchParams({ limit: "200" });
  if (operator) params.set("operator", operator);
  const r = await fetch(`${BASE}/towers?${params}`, { headers: headers() });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return (await r.json()).towers;
}

export async function fetchNearestTowers(lat, lon, limit = 5) {
  const params = new URLSearchParams({ lat, lon, limit });
  const r = await fetch(`${BASE}/towers/nearest?${params}`, { headers: headers() });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return (await r.json()).nearest_towers;
}

export async function analyzeLink(towerId, receiver) {
  const params = new URLSearchParams({ tower_id: towerId });
  const r = await fetch(`${BASE}/analyze?${params}`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(receiver),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export async function planRepeater(towerId, receiver, maxHops = 3) {
  const params = new URLSearchParams({ tower_id: towerId, max_hops: maxHops });
  const r = await fetch(`${BASE}/plan_repeater?${params}`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(receiver),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export function pdfReportUrl(towerId, lat, lon, height = 10, gain = 12) {
  const params = new URLSearchParams({
    tower_id: towerId, lat, lon, height_m: height, antenna_gain: gain,
  });
  return `${BASE}/export_report/pdf?${params}`;
}
