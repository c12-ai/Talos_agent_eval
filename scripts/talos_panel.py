"""
TALOS agent API helpers (API prefix: /api/).
"""

import json
import time
import requests

TALOS_BASE = "http://192.168.12.239:8080"
API_BASE = f"{TALOS_BASE}/api"
PHOENIX_BASE = "http://192.168.12.239:6006"

# Bypass proxy for internal IPs
REQ_KWARGS = {"proxies": {"http": None, "https": None}}


def create_session(title: str) -> str:
    r = requests.post(f"{API_BASE}/sessions", json={"title": title}, timeout=10, **REQ_KWARGS)
    r.raise_for_status()
    return r.json()["id"]


def get_workflow_state(session_id: str) -> dict:
    r = requests.get(f"{API_BASE}/sessions/{session_id}/workflow-state", timeout=10, **REQ_KWARGS)
    if r.status_code != 200:
        return {}
    return r.json()


def update_session_title(session_id: str, title: str) -> dict:
    r = requests.put(f"{API_BASE}/sessions/{session_id}", json={"title": title}, timeout=10, **REQ_KWARGS)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Phoenix
# ---------------------------------------------------------------------------

def post_annotation(span_id, name, label=None, score=None, explanation="", identifier="", metadata=None):
    body = {"data": [{
        "name": name, "annotator_kind": "HUMAN",
        "result": {"label": label, "score": score, "explanation": explanation},
        "metadata": metadata or {}, "identifier": identifier, "span_id": span_id,
    }]}
    r = requests.post(f"{PHOENIX_BASE}/v1/span_annotations?sync=true", json=body, timeout=10, **REQ_KWARGS)
    r.raise_for_status()
    return r.json()


def find_root_spans(session_id: str = None, limit: int = 100) -> list:
    """Find LangGraph root spans."""
    params = {"parent_id": "null", "name": "LangGraph", "limit": limit}
    r = requests.get(f"{PHOENIX_BASE}/v1/projects/UHJvamVjdDoy/spans", params=params, timeout=10, **REQ_KWARGS)
    r.raise_for_status()
    return r.json().get("data", [])


# ---------------------------------------------------------------------------
# PG lab status
# ---------------------------------------------------------------------------

try:
    import psycopg2
    _HAS_PG = True
except ImportError:
    _HAS_PG = False


def get_lab_run_status(lab_server_id: str):
    if not _HAS_PG:
        return None
    try:
        conn = psycopg2.connect(
            host="192.168.12.239", port=5432, dbname="labassistant_db",
            user="labassistant", password="labassistant", connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("SELECT status FROM runs WHERE lab_server_id=%s ORDER BY created_at DESC LIMIT 1", (lab_server_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"[PG] {e}")
        return None


def wait_for_lab_done(lab_server_id: str, timeout_sec: int = 900) -> tuple:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        s = get_lab_run_status(lab_server_id)
        if s in ("completed", "failed", "cancelled"):
            return True, s
        print(f"[PG] {lab_server_id} -> {s}, waiting...")
        time.sleep(10)
    return False, "timeout"
