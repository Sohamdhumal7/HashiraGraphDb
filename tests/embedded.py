"""Real FalkorDB engine (FalkorDBLite, embedded, no Docker) for integration tests."""
from __future__ import annotations
import os, tempfile, uuid
try:
    # pyrefly: ignore[missing-import]
    from redislite.falkordb_client import FalkorDB
    AVAILABLE = True
except Exception:  # FalkorDBLite targets Linux/macOS; on plain Windows these tests skip
    FalkorDB = None
    AVAILABLE = False

_DB = None

def get_db():
    global _DB
    if _DB is None:
        _DB = FalkorDB(os.path.join(tempfile.mkdtemp(), "hashira-test.db"))
    return _DB

def fresh_graph(prefix: str = "t"):
    return get_db().select_graph(f"{prefix}_{uuid.uuid4().hex[:10]}")

def seeded_graph():
    from seed_graph import seed_graph
    g = fresh_graph("seed")
    seed_graph(g)
    return g

def counts(graph):
    n = graph.ro_query("MATCH (n) RETURN count(n)").result_set[0][0]
    r = graph.ro_query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    return n, r
