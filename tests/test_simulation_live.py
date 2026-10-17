"""Opt-in live integration comparison; skipped in the default offline suite.

Run against the seeded demo graph with HASHIRAGRAPH_LIVE_TEST=1. It performs
read-only graph queries and checks classifications against a local BFS oracle.
"""
from __future__ import annotations

import os
import unittest
from collections import deque

from graph_store import close_client, connect_graph, query_rows
from simulation import simulate_failure


def bfs_reference(edges, failed, max_hops=4):
    nodes = set(edges)
    nodes.update(target for links in edges.values() for target, _ in links)
    statuses = {}
    for start in nodes - set(failed):
        queue = deque([(start, [], [start])])
        paths = []
        while queue:
            current, modes, path = queue.popleft()
            if len(modes) == max_hops:
                continue
            for dependency, mode in edges.get(current, []):
                next_modes, next_path = modes + [mode], path + [dependency]
                if dependency in failed:
                    paths.append(next_modes)
                elif dependency not in path:
                    queue.append((dependency, next_modes, next_path))
        if paths:
            statuses[start] = "DOWN" if any(all(mode == "hard" for mode in p) for p in paths) else "DEGRADED"
    return statuses


@unittest.skipUnless(os.getenv("HASHIRAGRAPH_LIVE_TEST") == "1", "set HASHIRAGRAPH_LIVE_TEST=1 to query the configured Cloud graph")
class LiveSimulatorIntegrationTests(unittest.TestCase):
    def test_seeded_scenarios_match_independent_bfs_and_leave_counts_unchanged(self):
        client, graph = connect_graph("hashira_demo")
        try:
            edge_rows = query_rows(graph,
                "MATCH (a:Service)-[r:DEPENDS_ON]->(b:Service) RETURN a.name,b.name,coalesce(r.mode,'unknown')",
                read_only=True)
            edges = {}
            for source, target, mode in edge_rows:
                edges.setdefault(source, []).append((target, mode))
            invalid_modes = query_rows(graph,
                "MATCH ()-[r:DEPENDS_ON]->() WHERE r.mode IS NULL OR (r.mode <> 'hard' AND r.mode <> 'soft') RETURN count(r)",
                read_only=True)
            self.assertEqual(int(invalid_modes[0][0] or 0) if invalid_modes else 0, 0)
            for failed in ("Payment API", "Ledger Service", "Fraud Service"):
                report = simulate_failure(graph, [failed])
                actual = {row["service"]: row["status"] for row in report["affected"]}
                self.assertEqual(actual, bfs_reference(edges, {failed}))
                self.assertTrue(report["source_graph_unchanged"])
        finally:
            close_client(client)


if __name__ == "__main__":
    unittest.main()
