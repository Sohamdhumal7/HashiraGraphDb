"""Offline simulator tests with an independent Python BFS graph oracle."""
from __future__ import annotations

import unittest

from simulation import PATH_QUERY, SimulationError, simulate_failure


class SyntheticGraph:
    def __init__(self, services, edges, owners=None):
        self.services = services  # name -> criticality
        self.edges = edges        # dependent -> [(dependency, mode)]
        self.owners = owners or {}
        self.read_queries = []
        self.write_queries = []

    def query(self, query, params=None):
        self.write_queries.append((query, params or {}))
        raise AssertionError("simulation must use read-only graph queries")

    def ro_query(self, query, params=None):
        params = params or {}
        self.read_queries.append((query, params))
        if "count(DISTINCT n)" in query:
            return Result([[len(self.services), sum(len(v) for v in self.edges.values())]])
        if "WHERE s.name IN $targets" in query:
            return Result([[name, self.services[name]] for name in params["targets"] if name in self.services])
        if "DEPENDS_ON*1..4" in query:
            rows = []
            for service, paths in bfs_paths(self.edges, params["failed"], 4).items():
                for failed, nodes, modes in paths:
                    rows.append([service, failed, len(modes), self.services[service], nodes, modes])
            return Result(rows)
        if "OWNS" in query:
            return Result([[service, owner] for service in params["services"] for owner in self.owners.get(service, [])])
        raise AssertionError(f"Unexpected read query: {query}")


class Result:
    def __init__(self, rows):
        self.result_set = rows


def bfs_paths(edges, failed, max_hops):
    """Independent breadth-first enumerator for dependent-to-dependency paths."""
    result = {}
    nodes = set(edges)
    nodes.update(dep for links in edges.values() for dep, _ in links)
    for start in nodes - set(failed):
        queue = [(start, [start], [])]
        while queue:
            current, path, modes = queue.pop(0)
            if len(modes) >= max_hops:
                continue
            for dependency, mode in edges.get(current, []):
                next_path, next_modes = path + [dependency], modes + [mode]
                if dependency in failed:
                    result.setdefault(start, []).append((dependency, next_path, next_modes))
                elif dependency not in path:
                    queue.append((dependency, next_path, next_modes))
    return result


def expected_classification(edges, failed, max_hops=4):
    return {service: ("DOWN" if any(all(mode == "hard" for mode in modes) for _, _, modes in paths) else "DEGRADED")
            for service, paths in bfs_paths(edges, failed, max_hops).items()}


class SimulationTests(unittest.TestCase):
    def test_hard_chain_and_evidence_paths(self):
        graph = SyntheticGraph({"Web": "high", "Checkout": "critical", "Payment": "critical", "Ledger": "critical"},
            {"Web": [("Checkout", "hard")], "Checkout": [("Payment", "hard")], "Payment": [("Ledger", "hard")]},
            {"Web": ["Web Team"], "Checkout": ["Checkout Team"]})
        result = simulate_failure(graph, ["Ledger"])
        self.assertEqual({r["service"]: r["status"] for r in result["affected"]}, {"Payment": "DOWN", "Checkout": "DOWN", "Web": "DOWN"})
        web = next(r for r in result["affected"] if r["service"] == "Web")
        self.assertEqual(web["path"], ["Web", "Checkout", "Payment", "Ledger"])
        self.assertEqual(web["modes"], ["hard", "hard", "hard"])
        self.assertEqual(web["owners"], ["Web Team"])

    def test_soft_edge_degrades_and_criticality_ranks_within_class(self):
        graph = SyntheticGraph({"Checkout": "critical", "Fraud": "high", "Web": "high"},
            {"Checkout": [("Fraud", "soft")], "Web": [("Checkout", "hard")]})
        result = simulate_failure(graph, ["Fraud"])
        self.assertEqual({r["service"]: r["status"] for r in result["affected"]}, {"Checkout": "DEGRADED", "Web": "DEGRADED"})
        self.assertEqual(result["affected"][0]["service"], "Checkout")
        self.assertEqual(result["affected"][1]["path"], ["Web", "Checkout", "Fraud"])

    def test_multiple_targets_and_down_precedes_degraded_path(self):
        edges = {"API": [("HardFail", "hard"), ("SoftFail", "soft")], "Web": [("API", "hard")], "Worker": [("SoftFail", "soft")]}
        graph = SyntheticGraph({name: "high" for name in {"API", "HardFail", "SoftFail", "Web", "Worker"}}, edges)
        result = simulate_failure(graph, ["HardFail", "SoftFail"])
        expected = expected_classification(edges, {"HardFail", "SoftFail"})
        self.assertEqual({row["service"]: row["status"] for row in result["affected"]}, expected)
        self.assertEqual(next(row for row in result["affected"] if row["service"] == "API")["status"], "DOWN")

    def test_four_hop_bound_and_unknown_mode_is_conservative(self):
        edges = {"A": [("B", "hard")], "B": [("C", "hard")], "C": [("D", "hard")], "D": [("E", "hard")], "E": [("F", "hard")], "X": [("F", "mystery")]}
        graph = SyntheticGraph({name: "medium" for name in "ABCDEF X".replace(" ", "")}, edges)
        result = simulate_failure(graph, ["F"])
        actual = {row["service"]: row["status"] for row in result["affected"]}
        self.assertNotIn("A", actual)
        self.assertEqual(actual["B"], "DOWN")
        self.assertEqual(actual["X"], "DEGRADED")
        self.assertEqual(result["max_hops"], 4)

    def test_missing_target_fails_and_no_targets_are_rejected(self):
        graph = SyntheticGraph({"Payment": "critical"}, {})
        with self.assertRaisesRegex(SimulationError, "Select at least one"):
            simulate_failure(graph, [])
        with self.assertRaisesRegex(SimulationError, "Unknown service"):
            simulate_failure(graph, ["Not A Service"])

    def test_read_only_and_health_snapshot(self):
        graph = SyntheticGraph({"A": "high", "B": "high"}, {"A": [("B", "hard")]})
        result = simulate_failure(graph, ["B"])
        self.assertFalse(graph.write_queries)
        self.assertEqual(len([q for q, _ in graph.read_queries if "count(DISTINCT n)" in q]), 2)
        self.assertTrue(result["source_graph_unchanged"])
        self.assertIn("[r IN relationships(p)", PATH_QUERY)

    def test_no_impact_is_reported_honestly(self):
        graph = SyntheticGraph({"Independent": "medium", "Target": "high"}, {})
        result = simulate_failure(graph, ["Target"])
        self.assertEqual(result["affected"], [])
        self.assertIn("No dependent services", result["message"])


if __name__ == "__main__":
    unittest.main()
