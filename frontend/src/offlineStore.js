/**
 * Offline storage layer using IndexedDB.
 *
 * Stores tower data, analysis results, and PDF blobs so the PWA
 * works without connectivity.
 *
 * @module offlineStore
 */

const DB_NAME = "ttp-offline";
const DB_VERSION = 1;

const STORE_TOWERS = "towers";
const STORE_ANALYSES = "analyses";
const STORE_PDFS = "pdfs";

/** @type {Promise<IDBDatabase>|null} */
let _dbPromise = null;

function openDB() {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_TOWERS)) {
        db.createObjectStore(STORE_TOWERS, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(STORE_ANALYSES)) {
        db.createObjectStore(STORE_ANALYSES, { keyPath: "key" });
      }
      if (!db.objectStoreNames.contains(STORE_PDFS)) {
        db.createObjectStore(STORE_PDFS, { keyPath: "key" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return _dbPromise;
}

// ── Generic helpers ────────────────────────────────────────

async function putAll(storeName, items) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, "readwrite");
    const store = tx.objectStore(storeName);
    for (const item of items) {
      store.put(item);
    }
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function getAll(storeName) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, "readonly");
    const store = tx.objectStore(storeName);
    const req = store.getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function put(storeName, item) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, "readwrite");
    const store = tx.objectStore(storeName);
    const req = store.put(item);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

async function get(storeName, key) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, "readonly");
    const store = tx.objectStore(storeName);
    const req = store.get(key);
    req.onsuccess = () => resolve(req.result ?? null);
    req.onerror = () => reject(req.error);
  });
}

// ── Tower data ─────────────────────────────────────────────

/**
 * Persist fetched tower list into IndexedDB.
 * @param {Array} towers
 */
export async function cacheTowers(towers) {
  try {
    await putAll(STORE_TOWERS, towers);
  } catch (e) {
    console.warn("offlineStore: failed to cache towers", e);
  }
}

/**
 * Load towers from IndexedDB (offline fallback).
 * @returns {Promise<Array>}
 */
export async function getCachedTowers() {
  try {
    return await getAll(STORE_TOWERS);
  } catch {
    return [];
  }
}

// ── Analysis results ───────────────────────────────────────

/** Build a deterministic cache key for an analysis request. */
function analysisKey(towerId, receiver) {
  return `${towerId}|${receiver.lat}|${receiver.lon}|${receiver.height_m}|${receiver.antenna_gain_dbi}`;
}

/**
 * Cache an analysis result keyed by tower + receiver params.
 */
export async function cacheAnalysis(towerId, receiver, result) {
  try {
    const key = analysisKey(towerId, receiver);
    await put(STORE_ANALYSES, { key, towerId, receiver, result, ts: Date.now() });
  } catch (e) {
    console.warn("offlineStore: failed to cache analysis", e);
  }
}

/**
 * Get a cached analysis result, or null.
 */
export async function getCachedAnalysis(towerId, receiver) {
  try {
    const key = analysisKey(towerId, receiver);
    const entry = await get(STORE_ANALYSES, key);
    return entry?.result ?? null;
  } catch {
    return null;
  }
}

// ── PDF blobs ──────────────────────────────────────────────

/**
 * Cache a PDF as an ArrayBuffer so it survives offline.
 */
export async function cachePdf(towerId, receiver, arrayBuffer) {
  try {
    const key = analysisKey(towerId, receiver);
    await put(STORE_PDFS, { key, towerId, receiver, pdf: arrayBuffer, ts: Date.now() });
  } catch (e) {
    console.warn("offlineStore: failed to cache PDF", e);
  }
}

/**
 * Get a cached PDF ArrayBuffer, or null.
 */
export async function getCachedPdf(towerId, receiver) {
  try {
    const key = analysisKey(towerId, receiver);
    const entry = await get(STORE_PDFS, key);
    return entry?.pdf ?? null;
  } catch {
    return null;
  }
}
