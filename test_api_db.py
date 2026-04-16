"""Quick HTTP test suite for telecom_tower_power_db.py endpoints."""
import io
import requests

BASE = "http://localhost:8001"
H = {"X-API-Key": "pro_abc"}
H_FREE = {"X-API-Key": "free_123"}
results = []


def test(name, method, url, expected_status, **kw):
    r = getattr(requests, method)(BASE + url, **kw)
    ok = r.status_code == expected_status
    results.append((name, r.status_code, expected_status, ok))
    return r


# 1. Health
test("GET /health", "get", "/health", 200)

# 2. List towers
r = test("GET /towers", "get", "/towers", 200, headers=H)
count = len(r.json()["towers"])
print(f"  towers: {count}")

# 3. Filter by operator (match)
r = test("GET /towers?operator=Claro", "get", "/towers?operator=Claro", 200, headers=H)
print(f"  filtered Claro: {len(r.json()['towers'])}")

# 4. Filter by operator (no match)
r = test("GET /towers?operator=NOPE", "get", "/towers?operator=NOPE", 200, headers=H)
print(f"  filtered NOPE: {len(r.json()['towers'])}")

# 5. Nearest towers
r = test("GET /towers/nearest", "get", "/towers/nearest?lat=-23.56&lon=-46.64&limit=3", 200, headers=H)
nearest = r.json()["nearest_towers"]
print(f"  nearest: {nearest[0]['id']}")

# 6. Analyze (success)
body = {"lat": -23.56, "lon": -46.64, "height_m": 10}
r = test("POST /analyze (200)", "post", "/analyze?tower_id=T001", 200, headers=H, json=body)
a = r.json()
print(f"  feasible={a['feasible']} signal={a['signal_dbm']}")

# 7. Analyze (tower not found)
test("POST /analyze (404)", "post", "/analyze?tower_id=NOEXIST", 404, headers=H, json=body)

# 8. No API key
test("No key (401)", "get", "/towers", 401)

# 9. Invalid API key
test("Bad key (401)", "get", "/towers", 401, headers={"X-API-Key": "bad"})

# 10. Free tier on batch_submit (skipped — requires Redis)
# files = {"csv_file": ("t.csv", io.BytesIO(b"lat,lon\n-23.56,-46.64"), "text/csv")}
# test("Free batch (403)", "post", "/batch_submit?tower_id=T001", 403, headers=H_FREE, files=files)

# 11. Invalid job ID (path traversal — skipped, requires Redis for Job.fetch)
# test("Traversal batch_status", "get", "/batch_status/..%2F..%2Fetc", 400, headers=H)

# 12. Path traversal on download (resolved by HTTP stack → 404 from router)
test("Traversal batch_download", "get", "/batch_download/..%2F..%2Fetc", 404, headers=H)

# 13. Add second tower
t2 = {"id": "T002", "lat": -22.9, "lon": -43.17, "height_m": 50,
      "operator": "TIM", "bands": ["1800MHz"], "power_dbm": 40}
test("POST /towers (T002)", "post", "/towers", 201, headers=H, json=t2)

# 14. Verify tower count
r = test("GET /towers (count)", "get", "/towers", 200, headers=H)
print(f"  total towers: {len(r.json()['towers'])}")

# 15. Metrics endpoint
r = test("GET /metrics", "get", "/metrics", 200)
has_auto = "http_requests_total" in r.text
has_custom = "batch_jobs_total" in r.text and "active_batch_jobs" in r.text
print(f"  auto metrics: {has_auto}, custom metrics: {has_custom}")

# Summary
print()
print(f"{'Test':<30} {'Got':>5} {'Exp':>5} {'Pass':>5}")
print("-" * 50)
all_pass = True
for name, got, exp, ok in results:
    status = "OK" if ok else "FAIL"
    print(f"{name:<30} {got:>5} {exp:>5} {status:>5}")
    if not ok:
        all_pass = False
print("-" * 50)
passed = sum(1 for _, _, _, ok in results if ok)
print(f"Result: {'ALL PASSED' if all_pass else 'SOME FAILED'} ({passed}/{len(results)})")
