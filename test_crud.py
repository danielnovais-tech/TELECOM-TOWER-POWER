# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Quick CRUD smoke test – run against a local API on port 8000."""
import os
import requests
import sys

BASE = os.getenv("TEST_API_BASE", "http://localhost:8001")
H = {"X-API-Key": os.getenv("TEST_API_KEY", "demo_ttp_pro_2604"), "Content-Type": "application/json"}
ok = True

def check(label, condition):
    global ok
    status = "PASS" if condition else "FAIL"
    if not condition:
        ok = False
    print(f"  [{status}] {label}")

print("=== HEALTH ===")
r = requests.get(f"{BASE}/health")
h = r.json()
print(f"  Backend: {h.get('db_backend','N/A')}, Towers: {h.get('towers_in_db', 'N/A')}")
check("health returns 200", r.status_code == 200)
check("db_backend field present", "db_backend" in h)

print("\n=== LIST TOWERS ===")
r = requests.get(f"{BASE}/towers", headers=H)
towers = r.json()["towers"]
print(f"  Count: {len(towers)}")
check("list returns towers", len(towers) >= 1)

print("\n=== GET SINGLE TOWER ===")
first_id = towers[0]["id"]
r = requests.get(f"{BASE}/towers/{first_id}", headers=H)
check(f"GET /towers/{first_id} => 200", r.status_code == 200)

print("\n=== CREATE TEST TOWER ===")
new = {"id": "TEST_CRUD", "lat": -10.0, "lon": -50.0,
       "height_m": 30, "operator": "Test", "bands": ["700MHz"], "power_dbm": 40}
r = requests.post(f"{BASE}/towers", json=new, headers=H)
print(f"  {r.status_code}: {r.json()}")
check("POST /towers => 201", r.status_code == 201)

print("\n=== UPDATE TEST TOWER ===")
new["height_m"] = 60
new["power_dbm"] = 46
r = requests.put(f"{BASE}/towers/TEST_CRUD", json=new, headers=H)
print(f"  {r.status_code}: {r.json()}")
check("PUT /towers/TEST_CRUD => 200", r.status_code == 200)

print("\n=== VERIFY UPDATE ===")
r = requests.get(f"{BASE}/towers/TEST_CRUD", headers=H)
t = r.json()
print(f"  height_m={t['height_m']}, power_dbm={t['power_dbm']}")
check("height_m updated to 60", t["height_m"] == 60)
check("power_dbm updated to 46", t["power_dbm"] == 46)

print("\n=== DELETE TEST TOWER ===")
r = requests.delete(f"{BASE}/towers/TEST_CRUD", headers=H)
print(f"  {r.status_code}: {r.json()}")
check("DELETE => 200", r.status_code == 200)

print("\n=== VERIFY DELETE ===")
r = requests.get(f"{BASE}/towers/TEST_CRUD", headers=H)
check("GET after delete => 404", r.status_code == 404)

print("\n=== DELETE NON-EXISTENT (expect 404) ===")
r = requests.delete(f"{BASE}/towers/NONEXISTENT", headers=H)
check("DELETE unknown => 404", r.status_code == 404)

print("\n=== FINAL HEALTH ===")
r = requests.get(f"{BASE}/health")
h = r.json()
print(f"  Towers: {h.get('towers_in_db', 'N/A')}")
check("health still returns 200", r.status_code == 200)

print("\n" + ("ALL TESTS PASSED" if ok else "SOME TESTS FAILED"))
sys.exit(0 if ok else 1)
