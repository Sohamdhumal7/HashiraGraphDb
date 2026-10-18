"""Integration tests on a REAL FalkorDB engine (embedded FalkorDBLite). Validates actual Cypher."""
import itertools, os, sys, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.embedded import AVAILABLE
if not AVAILABLE:
    raise unittest.SkipTest("FalkorDBLite not installed (pip install falkordblite; Linux/macOS/WSL)")
from tests.embedded import counts, fresh_graph, seeded_graph
import action_agent as aa
import curator_agent as cur
import graph_store as gs
import seed_graph as sg
import simulation as sim

NO_LLM = mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})


def oracle(failed, edges, max_hops=4):
    """Independent reference: enumerate simple edge-paths in plain Python."""
    adj = {}
    for a, b, m in edges: adj.setdefault(a, []).append((b, m))
    nodes = {n for a, b, _ in edges for n in (a, b)}
    down, deg = set(), set()
    def walk(start, cur, hops, all_hard, used):
        for b, m in adj.get(cur, []):
            if (cur, b) in used: continue
            h = hops + 1
            if b in failed:
                (down if all_hard and m == "hard" else deg).add(start)
            if h < max_hops: walk(start, b, h, all_hard and m == "hard", used | {(cur, b)})
    for s in nodes - set(failed): walk(s, s, 0, True, frozenset())
    return down, deg - down


class SeedTests(unittest.TestCase):
    def test_seed_counts_and_idempotency(self):
        g = seeded_graph(); first = counts(g)
        self.assertEqual(first, (19, 20))
        sg.seed_graph(g); sg.seed_graph(g)
        self.assertEqual(counts(g), first)

    def test_every_dependency_has_explicit_mode(self):
        g = seeded_graph()
        rows = g.ro_query("MATCH ()-[r:DEPENDS_ON]->() WHERE r.mode IS NULL RETURN count(r)").result_set
        self.assertEqual(rows[0][0], 0)

    def test_blast_radius_payment_api_matches_live_output(self):
        g = seeded_graph(); rows = sg.run_blast_radius_query(g, "Payment API")
        got = {r[0]: (r[1], r[2]) for r in rows}
        self.assertEqual(got["Checkout API"], ("Checkout Team", ["Checkout API", "Payment API"]))
        self.assertEqual(got["Web Checkout"], ("Web Team", ["Web Checkout", "Checkout API", "Payment API"]))
        self.assertEqual(len(rows), 2)

    def test_blast_radius_unknown_service_is_empty_not_error(self):
        self.assertEqual(sg.run_blast_radius_query(seeded_graph(), "Nope"), [])


class SimulationTests(unittest.TestCase):
    def setUp(self): self.g = seeded_graph()

    def _by(self, report): return {r["service"]: r for r in report["affected"]}

    def test_payment_api_scenario(self):
        r = sim.simulate_failure(self.g, ["Payment API"]); by = self._by(r)
        self.assertEqual({k: (v["status"], v["owners"], v["hops"]) for k, v in by.items()},
                         {"Checkout API": ("DOWN", ["Checkout Team"], 1), "Web Checkout": ("DOWN", ["Web Team"], 2)})
        self.assertTrue(r["source_graph_unchanged"])

    def test_ledger_scenario(self):
        by = self._by(sim.simulate_failure(self.g, ["Ledger Service"]))
        self.assertEqual({k: (v["status"], v["hops"]) for k, v in by.items()},
                         {"Payment API": ("DOWN", 1), "Checkout API": ("DOWN", 2), "Web Checkout": ("DOWN", 3)})
        self.assertEqual(by["Payment API"]["owners"], ["Payments Team"])

    def test_fraud_scenario_is_degraded_with_mixed_modes(self):
        by = self._by(sim.simulate_failure(self.g, ["Fraud Service"]))
        self.assertEqual({k: v["status"] for k, v in by.items()}, {"Checkout API": "DEGRADED", "Web Checkout": "DEGRADED"})
        self.assertEqual(by["Web Checkout"]["modes"], ["hard", "soft"])

    def test_leaf_with_no_dependents_reports_nothing(self):
        r = sim.simulate_failure(self.g, ["Web Checkout"])
        self.assertEqual(r["affected"], []); self.assertTrue(r["message"])

    def test_multi_target_down_takes_precedence_over_degraded(self):
        # Checkout API reaches Fraud softly but Payment API hard -> DOWN
        by = self._by(sim.simulate_failure(self.g, ["Fraud Service", "Payment API"]))
        self.assertEqual(by["Checkout API"]["status"], "DOWN"); self.assertEqual(by["Checkout API"]["failed_target"], "Payment API")

    def test_failed_targets_excluded_from_affected(self):
        by = self._by(sim.simulate_failure(self.g, ["Payment API", "Checkout API"]))
        self.assertNotIn("Checkout API", by); self.assertNotIn("Payment API", by)

    def test_ranking_down_first_then_criticality_then_hops(self):
        r = sim.simulate_failure(self.g, ["Identity Service", "Fraud Service", "Notification Service"])
        statuses = [a["status"] for a in r["affected"]]
        self.assertEqual(statuses, sorted(statuses, key=lambda s: s != "DOWN"))
        keys = [(a["status"] != "DOWN", -sim._criticality_rank(a["criticality"]), a["hops"], a["service"].casefold()) for a in r["affected"]]
        self.assertEqual(keys, sorted(keys))

    def test_invalid_scenarios(self):
        for bad in ([], ["", "  "], ["Nope"], ["Payment API", "Nope"]):
            with self.assertRaises(sim.SimulationError, msg=str(bad)): sim.simulate_failure(self.g, bad)

    def test_injection_style_target_is_inert(self):
        evil = "Payment API' }) DETACH DELETE n //"
        before = counts(self.g)
        with self.assertRaises(sim.SimulationError): sim.simulate_failure(self.g, [evil])
        self.assertEqual(counts(self.g), before)

    def test_simulation_is_read_only(self):
        before = counts(self.g)
        for t in sg.SERVICES: sim.simulate_failure(self.g, [t[0]])
        self.assertEqual(counts(self.g), before)
        props = self.g.ro_query("MATCH ()-[r:DEPENDS_ON]->() RETURN count(r) WHERE r.mode IS NOT NULL").result_set if False else None

    def test_every_single_and_pair_target_matches_independent_oracle(self):
        edges = list(sg.DEPENDENCIES); names = [s[0] for s in sg.SERVICES]
        combos = [[n] for n in names] + [list(c) for c in itertools.combinations(names, 2)]
        for failed in combos:
            r = sim.simulate_failure(self.g, failed)
            got_down = {a["service"] for a in r["affected"] if a["status"] == "DOWN"}
            got_deg = {a["service"] for a in r["affected"] if a["status"] == "DEGRADED"}
            exp_down, exp_deg = oracle(set(failed), edges)
            self.assertEqual((got_down, got_deg), (exp_down, exp_deg), failed)

    def test_unknown_or_missing_mode_is_conservative(self):
        g = fresh_graph("modes")
        g.query("CREATE (:Service {name:'A',criticality:'high'})-[:DEPENDS_ON]->(:Service {name:'B',criticality:'high'})")
        r = sim.simulate_failure(g, ["B"])
        self.assertEqual(r["affected"][0]["status"], "DEGRADED"); self.assertEqual(r["affected"][0]["modes"], ["unknown"])

    def test_hop_bound_is_four(self):
        g = fresh_graph("chain")
        for i in range(6): g.query("CREATE (:Service {name:$n,criticality:'low'})", {"n": f"S{i}"})
        for i in range(5):
            g.query("MATCH (a:Service {name:$a}),(b:Service {name:$b}) CREATE (a)-[:DEPENDS_ON {mode:'hard'}]->(b)", {"a": f"S{i}", "b": f"S{i+1}"})
        names = {a["service"] for a in sim.simulate_failure(g, ["S5"])["affected"]}
        self.assertEqual(names, {"S1", "S2", "S3", "S4"})  # S0 is 5 hops away

    def test_cycle_terminates_and_matches_oracle(self):
        g = fresh_graph("cycle")
        for n in "ABC": g.query("CREATE (:Service {name:$n,criticality:'low'})", {"n": n})
        edges = [("A", "B", "hard"), ("B", "C", "hard"), ("C", "A", "hard")]
        for a, b, m in edges: g.query("MATCH (x:Service {name:$a}),(y:Service {name:$b}) CREATE (x)-[:DEPENDS_ON {mode:$m}]->(y)", {"a": a, "b": b, "m": m})
        r = sim.simulate_failure(g, ["C"]); exp_down, exp_deg = oracle({"C"}, edges)
        self.assertEqual({a["service"] for a in r["affected"] if a["status"] == "DOWN"}, exp_down)

    def test_closed_ownership_is_not_reported_as_owner(self):
        self.g.query("MATCH (:Team {name:'Checkout Team'})-[r:OWNS]->(:Service {name:'Checkout API'}) SET r.status='closed'")
        by = self._by(sim.simulate_failure(self.g, ["Payment API"]))
        self.assertEqual(by["Checkout API"]["owners"], [])

    def test_simulation_detects_concurrent_write(self):
        class Racy:
            def __init__(s, g): s.g, s.n = g, 0
            name = "racy"
            def ro_query(s, q, p=None):
                if "RETURN count(DISTINCT n)" in q:
                    s.n += 1
                    if s.n == 2: s.g.query("CREATE (:Service {name:'Late'})" )
                return s.g.ro_query(q, p or {})
        r = sim.simulate_failure(Racy(self.g), ["Payment API"]); self.assertFalse(r["source_graph_unchanged"])


@NO_LLM
class AgentMemoryTests(unittest.TestCase):
    def setUp(self):
        self.g = seeded_graph(); self.a1 = aa.ActionAgent(self.g, "hashira_demo", "session-one")

    def _eps(self, g=None, **kw):
        return (g or self.g).ro_query("MATCH (m:MemoryEpisode) RETURN m.kind, m.source, m.summary, m.session_id, m.tenant_id").result_set

    def test_remember_saves_exactly_one_user_note(self):
        r = self.a1.ask("Remember this project note: HashiraGraph is our hackathon project, and Payment API is the primary service in our demo.")
        self.assertEqual(r["answer"], "Saved your note to persistent memory."); self.assertEqual(len(r["trace"]["episode_ids"]), 1)
        eps = self._eps(); self.assertEqual(len(eps), 1); self.assertEqual((eps[0][0], eps[0][1]), ("user_note", "User project note"))

    def test_note_links_to_exactly_one_canonical_service(self):
        self.a1.ask("Remember: Payment API is the primary service in our demo")
        rows = self.g.ro_query("MATCH (:MemoryEpisode)-[:MENTIONS]->(s:Service) RETURN s.name").result_set
        self.assertEqual(rows, [["Payment API"]])

    def test_note_with_two_services_is_not_linked_ambiguously(self):
        self.a1.ask("Remember: Payment API calls Ledger Service")
        self.assertEqual(self.g.ro_query("MATCH ()-[r:MENTIONS]->() RETURN count(r)").result_set[0][0], 0)

    def test_cross_session_recall_of_the_reported_note(self):
        self.a1.ask("Remember this project note: HashiraGraph is our hackathon project, and Payment API is the primary service in our demo.")
        a2 = aa.ActionAgent(self.g, "hashira_demo", "session-two")
        r = a2.ask("What did I tell you about the hackathon?")
        self.assertEqual(r["trace"]["tool"], "memories"); self.assertGreaterEqual(r["trace"]["rows"], 1)
        self.assertIn("hackathon project", r["answer"])

    def test_service_scoped_recall_uses_link_or_text(self):
        self.a1.ask("Remember: Payment API is the primary service in our demo"); self.a1.ask("Remember: lunch is at noon")
        r = aa.ActionAgent(self.g, "hashira_demo", "s3").ask("What do we know about Payment API?")
        kinds_msgs = [e["user_message"] for e in r["evidence"]]
        self.assertTrue(any("primary service" in m for m in kinds_msgs)); self.assertFalse(any("lunch" in m for m in kinds_msgs))

    def test_current_session_filter(self):
        self.a1.ask("Remember: first note in session one")
        a2 = aa.ActionAgent(self.g, "hashira_demo", "session-two"); a2.ask("Remember: second note in session two")
        r = a2.ask("What did I just say in this session?")
        sessions = {e["session_id"] for e in r["evidence"]}; self.assertEqual(sessions, {"session-two"})

    def test_tenant_isolation_between_graphs_and_property(self):
        other = seeded_graph(); b = aa.ActionAgent(other, "demo_acme", "x")
        self.a1.ask("Remember: tenant-A-secret-note")
        r = b.ask("What did I tell you about the secret note?")
        self.assertNotIn("tenant-A-secret-note", repr(r["evidence"]) + r["answer"])
        self.assertEqual([e for e in self._eps(other) if e[0] == "user_note"], [])
        # same graph, wrong tenant property: must not be visible either
        self.g.query("CREATE (:MemoryEpisode {id:'x',tenant_id:'demo_acme',session_id:'z',created_at:'2026-01-01',kind:'user_note',summary:'LEAK',user_message:'LEAK',source:'t'})")
        r = self.a1.ask("What do you know?"); self.assertNotIn("LEAK", repr(r["evidence"]))

    def test_each_ask_persists_one_exchange_not_two(self):
        self.a1.ask("Who owns Payment API?")
        eps = [e for e in self._eps() if e[0] == "exchange"]; self.assertEqual(len(eps), 1)

    def test_repeated_question_creates_distinct_episodes(self):
        for _ in range(3): self.a1.ask("Who owns Payment API?")
        self.assertEqual(len([e for e in self._eps() if e[0] == "exchange"]), 3)

    def test_persist_episode_is_idempotent_and_keeps_original_metadata(self):
        aa.persist_episode(self.g, "hashira_demo", "s", "m1", "ans1", [], source="User project note", kind="user_note", episode_id="fixed")
        aa.persist_episode(self.g, "hashira_demo", "s", "m2", "ans2", [], source="HashiraGraph Action Agent", kind="exchange", episode_id="fixed")
        rows = self.g.ro_query("MATCH (m:MemoryEpisode {id:'fixed'}) RETURN m.kind,m.source,m.summary").result_set
        self.assertEqual(rows, [["user_note", "User project note", "ans1"]])

    def test_persist_rejects_unknown_kind(self):
        with self.assertRaises(ValueError): aa.persist_episode(self.g, "hashira_demo", "s", "m", "a", [], kind="admin")

    def test_failed_write_is_reported_not_claimed(self):
        class ReadOnlyGraph:
            name = "ro"
            def __init__(s, g): s.g = g
            def ro_query(s, q, p=None): return s.g.ro_query(q, p or {})
            def query(s, q, p=None): raise RuntimeError("READONLY")
        r = aa.ActionAgent(ReadOnlyGraph(self.g), "hashira_demo", "s").ask("Remember: this must not be claimed")
        self.assertIn("couldn't save", r["answer"]); self.assertIn("error", r["trace"])
        r = aa.ActionAgent(ReadOnlyGraph(self.g), "hashira_demo", "s").ask("Who owns Payment API?")
        self.assertIn("memory_write_error", r["trace"]); self.assertIn("Payments Team", r["answer"])

    def test_empty_recall_is_honest(self):
        r = self.a1.ask("What did I tell you about the hackathon?")
        self.assertEqual(r["trace"]["rows"], 0); self.assertIn("no matching records", r["answer"])

    def test_query_error_differs_from_empty(self):
        class Broken:
            name = "b"
            def ro_query(s, *a, **k): raise RuntimeError("Invalid input near 'WHERE'")
            def query(s, *a, **k): raise RuntimeError("write down")
        r = aa.ActionAgent(Broken(), "hashira_demo", "s").ask("Who owns Payment API?")
        self.assertIn("error", r["trace"]); self.assertIn("did not complete", r["answer"])

    def test_cypher_injection_in_question_is_inert(self):
        before = counts(self.g)
        self.a1.ask("Who owns Payment API' }) DETACH DELETE s //")
        self.a1.ask("Remember: x'}) MATCH (n) DETACH DELETE n //")
        nodes_after = self.g.ro_query("MATCH (n) WHERE NOT n:MemoryEpisode RETURN count(n)").result_set[0][0]
        self.assertEqual(nodes_after, before[0])

    def test_ownership_incidents_decisions_tools(self):
        r = self.a1.ask("Who owns Payment API?"); self.assertEqual(r["evidence"][0]["team"], "Payments Team")
        r = self.a1.ask("Which incidents affected Payment API?"); self.assertEqual(r["trace"]["tool"], "incidents"); self.assertEqual(r["evidence"][0]["id"], "INC-1001")
        r = self.a1.ask("Which decisions govern Ledger Service?"); self.assertEqual(r["evidence"][0]["id"], "ADR-0007")
        r = self.a1.ask("Which decisions govern Payment API?"); self.assertEqual(r["evidence"], [])

    def test_blast_radius_via_agent_with_paths_for_viewer(self):
        r = self.a1.ask("If the Payment API fails, which upstream services may be affected, and which teams own them?")
        self.assertEqual(r["trace"]["tool"], "blast_radius")
        edges = {(a, rel, b) for row in r["evidence"] for a, rel, b in zip(row["nodes"], row["edges"], row["nodes"][1:])}
        self.assertEqual(edges, {("Web Checkout", "DEPENDS_ON", "Checkout API"), ("Checkout API", "DEPENDS_ON", "Payment API")})


class CuratorTests(unittest.TestCase):
    def setUp(self): self.g = seeded_graph()

    def test_clean_seed_no_changes_and_reports_honestly(self):
        before = counts(self.g); rep = cur.run_curator(self.g, "hashira_demo")
        self.assertEqual((rep["generic_relationships_promoted"], rep["ownership_facts_closed_with_explicit_ended_at"], rep["memory_rollups_created"]), (0, 0, 0))
        self.assertEqual(counts(self.g), before)

    def test_promotion_requires_verified_matching_labels_and_is_idempotent(self):
        self.g.query("MATCH (a:Service {name:'Fraud Service'}),(b:Service {name:'Identity Service'}) CREATE (a)-[:RELATED_TO {verified:true, canonical_type:'DEPENDS_ON', source:'rfc-1'}]->(b)")
        self.g.query("MATCH (a:Team {name:'Web Team'}),(b:Service {name:'Fraud Service'}) CREATE (a)-[:RELATED_TO {verified:true, canonical_type:'DEPENDS_ON', source:'bad-labels'}]->(b)")
        self.g.query("MATCH (a:Service {name:'Fraud Service'}),(b:Service {name:'Ledger Service'}) CREATE (a)-[:RELATED_TO {verified:false, canonical_type:'DEPENDS_ON', source:'unverified'}]->(b)")
        r1 = cur.run_curator(self.g, "hashira_demo"); self.assertEqual(r1["generic_relationships_promoted"], 1)
        self.assertEqual(self.g.ro_query("MATCH (:Service {name:'Fraud Service'})-[r:DEPENDS_ON {source_provenance:'rfc-1'}]->(:Service {name:'Identity Service'}) RETURN count(r)").result_set[0][0], 1)
        self.assertEqual(self.g.ro_query("MATCH ()-[r:RELATED_TO]->() RETURN count(r)").result_set[0][0], 3)  # originals retained
        self.assertEqual(self.g.ro_query("MATCH ()-[r:DEPENDS_ON {source_provenance:'bad-labels'}]->() RETURN count(r)").result_set[0][0], 0)
        r2 = cur.run_curator(self.g, "hashira_demo"); self.assertEqual(r2["generic_relationships_promoted"], 0)
        self.assertEqual(self.g.ro_query("MATCH ()-[r:DEPENDS_ON {source_provenance:'rfc-1'}]->() RETURN count(r)").result_set[0][0], 1)

    def test_promoted_edge_has_no_mode_so_simulation_treats_it_conservatively(self):
        self.g.query("MATCH (a:Service {name:'Fraud Service'}),(b:Service {name:'Identity Service'}) CREATE (a)-[:RELATED_TO {verified:true, canonical_type:'DEPENDS_ON', source:'rfc-1'}]->(b)")
        cur.run_curator(self.g, "hashira_demo")
        by = {a["service"]: a for a in sim.simulate_failure(self.g, ["Identity Service"])["affected"]}
        self.assertEqual(by["Fraud Service"]["status"], "DEGRADED")

    def test_ownership_closure_only_with_explicit_ended_at(self):
        self.g.query("MATCH (:Team {name:'Platform Team'})-[r:OWNS]->(:Service {name:'Identity Service'}) SET r.ended_at='2026-09-01'")
        rep = cur.run_curator(self.g, "hashira_demo"); self.assertEqual(rep["ownership_facts_closed_with_explicit_ended_at"], 1)
        st = self.g.ro_query("MATCH (:Team)-[r:OWNS]->(:Service {name:'Identity Service'}) RETURN r.status").result_set
        self.assertEqual(st, [["closed"]])
        self.assertEqual(self.g.ro_query("MATCH (:Team)-[r:OWNS]->() WHERE r.status='active' RETURN count(r)").result_set[0][0], 6)
        self.assertEqual(cur.run_curator(self.g, "hashira_demo")["ownership_facts_closed_with_explicit_ended_at"], 0)

    @NO_LLM
    def test_rollup_links_sources_never_deletes_and_tracks_new_episodes(self):
        a = aa.ActionAgent(self.g, "hashira_demo", "s")
        for i in range(3): a.ask(f"Remember: note {i}")
        n_before = self.g.ro_query("MATCH (m:MemoryEpisode) RETURN count(m)").result_set[0][0]
        r1 = cur.run_curator(self.g, "hashira_demo"); self.assertEqual(r1["memory_rollups_created"], 1)
        a.ask("Remember: a later note")
        cur.run_curator(self.g, "hashira_demo")
        self.assertEqual(self.g.ro_query("MATCH (m:MemoryEpisode) RETURN count(m)").result_set[0][0], n_before + 1)  # nothing deleted
        rollups = self.g.ro_query("MATCH (r:MemoryRollup {tenant_id:'hashira_demo'}) RETURN count(r)").result_set[0][0]
        self.assertEqual(rollups, 1)
        linked = self.g.ro_query("MATCH (:MemoryEpisode)-[:INCLUDED_IN]->(r:MemoryRollup) RETURN count(*)").result_set[0][0]
        stored = self.g.ro_query("MATCH (r:MemoryRollup) RETURN r.source_count").result_set[0][0]
        self.assertEqual(stored, linked, "rollup.source_count must equal number of linked episodes after re-run")


class DuplicateAndHealthTests(unittest.TestCase):
    def test_graph_health_counts_match_direct_counts(self):
        g = seeded_graph(); h = gs.graph_health(g); self.assertEqual((h["nodes"], h["relationships"]), counts(g))

    def test_health_of_missing_graph_is_zero_not_crash(self):
        self.assertEqual(gs.graph_health(fresh_graph("never")), {"nodes": 0, "relationships": 0})


if __name__ == "__main__":
    unittest.main()
