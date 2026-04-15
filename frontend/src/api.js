/**
 * Typed API client — powered by openapi-fetch + auto-generated types.
 *
 * Regenerate types after API changes:
 *   npm run generate:api
 *
 * @module api
 */

import createClient from "openapi-fetch";
import {
  cacheTowers, getCachedTowers,
  cacheAnalysis, getCachedAnalysis,
  cachePdf, getCachedPdf,
} from "./offlineStore";
/** @typedef {import("./api-schema").paths} paths */
/** @typedef {import("./api-schema").components} components */

/**
 * @typedef {components["schemas"]["TowerInput"]} Tower
 * @typedef {components["schemas"]["ReceiverInput"]} ReceiverInput
 * @typedef {components["schemas"]["LinkAnalysisResponse"]} LinkAnalysisResponse
 * @typedef {components["schemas"]["Band"]} Band
 */

const BASE = "/api";
let apiKey =
  localStorage.getItem("api_key") ||
  import.meta.env.VITE_API_KEY ||
  "demo-key-pro-001";

/** @type {import("openapi-fetch").Client<paths>} */
let client = createClient({
  baseUrl: BASE,
  headers: {
    "X-API-Key": apiKey,
    "Content-Type": "application/json",
  },
});

/**
 * Update the API key used for all subsequent requests.
 * Also persists to localStorage so it survives page reloads.
 * @param {string} key
 */
export function setApiKey(key) {
  apiKey = key;
  localStorage.setItem("api_key", key);
  client = createClient({
    baseUrl: BASE,
    headers: {
      "X-API-Key": apiKey,
      "Content-Type": "application/json",
    },
  });
}

// ── Helper: unwrap response or throw ────────────────────────────────

/** @type {{ remaining: number|null, limit: number|null }} */
export const rateLimit = { remaining: null, limit: null };

/** Custom error thrown on HTTP 429 so callers can show upgrade prompts. */
export class RateLimitError extends Error {
  constructor(limit) {
    super(
      `Rate limit exceeded (${limit} requests/min). Upgrade your plan for higher limits.`
    );
    this.name = "RateLimitError";
    this.limit = limit;
  }
}

/**
 * Capture X-RateLimit-* headers from any Response and update the
 * shared rateLimit object.  Returns the response unchanged.
 */
function captureRateLimit(res) {
  const headers = res.response?.headers ?? res.headers;
  if (!headers) return res;
  const rem = headers.get?.("x-ratelimit-remaining") ?? headers["x-ratelimit-remaining"];
  const lim = headers.get?.("x-ratelimit-limit") ?? headers["x-ratelimit-limit"];
  if (rem != null) rateLimit.remaining = Number(rem);
  if (lim != null) rateLimit.limit = Number(lim);
  // Notify listeners (if any)
  _rateLimitListeners.forEach((fn) => fn({ ...rateLimit }));
  return res;
}

/** @type {Set<(info: {remaining:number|null,limit:number|null}) => void>} */
const _rateLimitListeners = new Set();

/** Subscribe to rate-limit updates. Returns an unsubscribe function. */
export function onRateLimitChange(fn) {
  _rateLimitListeners.add(fn);
  return () => _rateLimitListeners.delete(fn);
}

function unwrap(result) {
  captureRateLimit(result);
  // Detect 429 from openapi-fetch error responses
  if (result.response?.status === 429) {
    throw new RateLimitError(rateLimit.limit);
  }
  if (result.error) {
    const detail =
      typeof result.error === "object" && result.error !== null
        ? result.error.detail || JSON.stringify(result.error)
        : String(result.error);
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return result.data;
}

// ── Public API functions ────────────────────────────────────────────

export async function fetchHealth() {
  const res = await client.GET("/health");
  return unwrap(res);
}

export async function fetchTowers(operator = null) {
  try {
    const res = await client.GET("/towers", {
      params: { query: { limit: 200, ...(operator ? { operator } : {}) } },
    });
    const towers = unwrap(res)?.towers;
    if (towers && towers.length) {
      cacheTowers(towers).catch(() => {});
    }
    return towers;
  } catch (e) {
    // Offline fallback: serve from IndexedDB
    const cached = await getCachedTowers();
    if (cached && cached.length) {
      console.info("offlineStore: serving towers from cache");
      return cached;
    }
    throw e;
  }
}

export async function fetchNearestTowers(lat, lon, limit = 5) {
  const res = await client.GET("/towers/nearest", {
    params: { query: { lat, lon, limit } },
  });
  return unwrap(res)?.nearest_towers;
}

export async function analyzeLink(towerId, receiver) {
  try {
    const res = await client.POST("/analyze", {
      params: { query: { tower_id: towerId } },
      body: receiver,
    });
    const result = unwrap(res);
    cacheAnalysis(towerId, receiver, result).catch(() => {});
    return result;
  } catch (e) {
    // Offline fallback: return cached analysis if available
    const cached = await getCachedAnalysis(towerId, receiver);
    if (cached) {
      console.info("offlineStore: serving analysis from cache");
      cached._fromCache = true;
      return cached;
    }
    throw e;
  }
}

export async function planRepeater(towerId, receiver, maxHops = 3) {
  const res = await client.POST("/plan_repeater", {
    params: { query: { tower_id: towerId, max_hops: maxHops } },
    body: receiver,
  });
  return unwrap(res);
}

/**
 * Download a PDF report via fetch (with API key header) and return a Blob URL.
 * The caller can use this URL in window.open() or an anchor download.
 */
export async function downloadPdfReport(towerId, lat, lon, height = 10, gain = 12) {
  const receiver = { lat, lon, height_m: height, antenna_gain_dbi: gain };
  try {
    const params = new URLSearchParams({
      tower_id: towerId, lat, lon, height_m: height, antenna_gain: gain,
    });
    const r = await fetch(`${BASE}/export_report/pdf?${params}`, {
      headers: { "X-API-Key": apiKey },
    });
    const rem = r.headers.get("x-ratelimit-remaining");
    const lim = r.headers.get("x-ratelimit-limit");
    if (rem != null) rateLimit.remaining = Number(rem);
    if (lim != null) rateLimit.limit = Number(lim);
    _rateLimitListeners.forEach((fn) => fn({ ...rateLimit }));
    if (r.status === 429) throw new RateLimitError(rateLimit.limit);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    const blob = await r.blob();
    // Cache the PDF for offline use
    blob.arrayBuffer().then((buf) => cachePdf(towerId, receiver, buf)).catch(() => {});
    return URL.createObjectURL(blob);
  } catch (e) {
    // Offline fallback: serve cached PDF
    const cachedBuf = await getCachedPdf(towerId, receiver);
    if (cachedBuf) {
      console.info("offlineStore: serving PDF from cache");
      const blob = new Blob([cachedBuf], { type: "application/pdf" });
      return URL.createObjectURL(blob);
    }
    throw e;
  }
}

// ── Batch job helpers ──────────────────────────────────────────

export async function submitBatchReport(towerId, csvFile) {
  // multipart/form-data upload — not easily typed via openapi-fetch,
  // kept as raw fetch.
  const form = new FormData();
  form.append("tower_id", towerId);
  form.append("file", csvFile);
  const r = await fetch(`${BASE}/batch_reports`, {
    method: "POST",
    headers: { "X-API-Key": apiKey },
    body: form,
  });
  // Capture rate-limit headers from raw fetch
  const rem = r.headers.get("x-ratelimit-remaining");
  const lim = r.headers.get("x-ratelimit-limit");
  if (rem != null) rateLimit.remaining = Number(rem);
  if (lim != null) rateLimit.limit = Number(lim);
  _rateLimitListeners.forEach((fn) => fn({ ...rateLimit }));
  if (r.status === 429) throw new RateLimitError(rateLimit.limit);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export async function pollJobStatus(jobId) {
  const res = await client.GET("/jobs/{job_id}", {
    params: { path: { job_id: jobId } },
  });
  return unwrap(res);
}

/**
 * Download batch job results via fetch (with API key header) and return a Blob URL.
 */
export async function downloadJobResult(jobId) {
  const r = await fetch(`${BASE}/jobs/${jobId}/download`, {
    headers: { "X-API-Key": apiKey },
  });
  const rem = r.headers.get("x-ratelimit-remaining");
  const lim = r.headers.get("x-ratelimit-limit");
  if (rem != null) rateLimit.remaining = Number(rem);
  if (lim != null) rateLimit.limit = Number(lim);
  _rateLimitListeners.forEach((fn) => fn({ ...rateLimit }));
  if (r.status === 429) throw new RateLimitError(rateLimit.limit);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  const blob = await r.blob();
  return URL.createObjectURL(blob);
}

/**
 * Open a WebSocket to stream live progress for a batch job.
 * (WebSocket endpoints are not part of the OpenAPI spec — kept as raw WS.)
 * @param {string} jobId
 * @param {(msg: object) => void} onMessage  – called with each progress frame
 * @param {(err?: Event) => void} [onClose]  – called when the socket closes
 * @returns {{ close: () => void }}           – call .close() to disconnect early
 */
export function watchJobProgress(jobId, onMessage, onClose) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(
    `${proto}//${location.host}${BASE}/jobs/${jobId}/ws?token=${encodeURIComponent(apiKey)}`
  );

  ws.onmessage = (evt) => {
    try {
      onMessage(JSON.parse(evt.data));
    } catch { /* ignore malformed frames */ }
  };
  ws.onclose = (evt) => onClose?.(evt);
  ws.onerror = (evt) => onClose?.(evt);

  return { close: () => ws.close() };
}

// ── Bedrock AI Playground helpers ────────────────────────────────

/**
 * Send a prompt to the Amazon Bedrock playground endpoint.
 * @param {{ prompt: string, model_id?: string, max_tokens?: number, temperature?: number, context?: string }} body
 * @returns {Promise<{ response: string, model_id: string, input_tokens: number, output_tokens: number }>}
 */
export async function bedrockChat(body) {
  const r = await fetch(`${BASE}/bedrock/chat`, {
    method: "POST",
    headers: { "X-API-Key": apiKey, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const rem = r.headers.get("x-ratelimit-remaining");
  const lim = r.headers.get("x-ratelimit-limit");
  if (rem != null) rateLimit.remaining = Number(rem);
  if (lim != null) rateLimit.limit = Number(lim);
  _rateLimitListeners.forEach((fn) => fn({ ...rateLimit }));
  if (r.status === 429) throw new RateLimitError(rateLimit.limit);
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(detail || `${r.status} ${r.statusText}`);
  }
  return r.json();
}

/**
 * Fetch available Bedrock foundation models.
 * @returns {Promise<{ models: Array<{ model_id: string, provider: string, name: string }> }>}
 */
export async function fetchBedrockModels() {
  const r = await fetch(`${BASE}/bedrock/models`, {
    headers: { "X-API-Key": apiKey },
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
