"""Streamlit dashboard for the real FalkorDB-backed demo."""
from __future__ import annotations

import json
import uuid

import streamlit as st

from action_agent import ActionAgent
from curator_agent import run_curator
from graph_store import close_client, connect_graph, graph_health, query_rows
from simulation import list_services, simulate_failure

TENANTS = {"hashira_demo": "Hashira demo", "demo_acme": "Synthetic Acme tenant"}
st.set_page_config(page_title="HashiraGraph", page_icon="🕸️", layout="wide")
st.title("🕸️ HashiraGraph")
st.caption("Company knowledge, service dependencies, and persistent agent memory — grounded in FalkorDB.")

tenant_id = st.sidebar.selectbox("Authorized demo tenant", list(TENANTS), format_func=lambda key: TENANTS[key])
st.sidebar.caption("These are fixed synthetic demo tenants. Graph-name separation is not a production identity system.")
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
if "messages" not in st.session_state:
    st.session_state.messages = []

client = graph = None
try:
    client, graph = connect_graph(tenant_id)
    health = graph_health(graph)
except Exception as exc:
    graph = None
    st.error(f"FalkorDB is unavailable: {type(exc).__name__}: {exc}")
    st.info("Check the connection fields in .env, then restart Streamlit. The app does not substitute an in-memory graph.")

left, right = st.columns([1.15, 0.85], gap="large")
with left:
    st.subheader("Agent Chat")
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    prompt = st.chat_input("Ask about service ownership, blast radius, incidents, decisions, or saved memories", disabled=graph is None)
    if prompt and graph is not None:
        st.session_state.messages.append({"role": "user", "content": prompt})
        result = ActionAgent(graph, tenant_id, st.session_state.session_id).ask(prompt)
        st.session_state.last_result = result
        st.session_state.messages.append({"role": "assistant", "content": result["answer"]})
        if result.get("trace", {}).get("memory_write_error"):
            st.warning("The response completed, but the exchange could not be saved to persistent memory.")
        # Explicit rerun skips the script's trailing cleanup, so close first.
        close_client(client)
        client = None
        st.rerun()
    if st.session_state.messages:
        last = st.session_state.messages[-1]
        # Details for most recent answer are kept for display only; the graph remains persistence.
        if last["role"] == "assistant" and "last_result" in st.session_state:
            result = st.session_state.last_result
            st.markdown("**Evidence provenance**")
            st.json(result.get("evidence", []))
            with st.expander("Multi-hop reasoning trace / query"):
                st.json(result.get("trace", {}))
            with st.expander("Self-Healing Cypher Log"):
                trace = result.get("trace", {})
                st.write("Query plan comes from the fixed, parameterized tool allowlist; generated Cypher is disabled.")
                if trace.get("error"):
                    st.error(f"Query failed and was reported without claiming success: {trace['error']}")
                else:
                    st.write(f"Executed `{trace.get('tool')}`; returned {trace.get('rows', 0)} evidence row(s).")
                st.code(trace.get("query") or "No query executed", language="cypher")
                for note in trace.get("notes", []):
                    st.caption(note)
    if graph is not None:
        st.caption("Cypher is selected from a fixed allowlist. Gemini can classify intent and phrase answers; it cannot submit arbitrary queries.")

with right:
    st.subheader("Graph Health")
    if graph is not None:
        m1, m2 = st.columns(2)
        m1.metric("Nodes", health["nodes"])
        m2.metric("Relationships", health["relationships"])
        st.caption(f"Live counts from graph `{graph.name}`")
        st.subheader("What-If Failure Simulator")
        st.caption("Hypothetical read-only analysis. It does not write or commit changes to the graph.")
        try:
            service_options = list_services(graph)
            chosen_targets = st.multiselect("Service failure target(s)", service_options, key=f"sim_targets_{tenant_id}")
            if st.button("Simulate Failure", disabled=not chosen_targets, key=f"simulate_{tenant_id}"):
                st.session_state.simulation_report = simulate_failure(graph, chosen_targets)
                st.session_state.simulation_tenant = tenant_id
            report = st.session_state.get("simulation_report")
            if report and st.session_state.get("simulation_tenant") == tenant_id:
                st.markdown("**Hypothetical failed targets**")
                st.write(", ".join(row["service"] for row in report["failed_targets"]))
                if report["affected"]:
                    st.dataframe([{"service": row["service"], "status": row["status"],
                                   "criticality": row["criticality"] or "unknown", "owners": ", ".join(row["owners"]) or "unassigned",
                                   "hops": row["hops"], "failed target": row["failed_target"],
                                   "evidence path": " → ".join(row["path"]), "modes": " → ".join(row["modes"])}
                                  for row in report["affected"]], width="stretch", hide_index=True)
                    with st.expander("All discovered evidence paths"):
                        for row in report["affected"]:
                            st.write(f"**{row['service']} — {row['status']}**")
                            for path in row["evidence_paths"]:
                                st.code(" → ".join(path["path"]) + "\nModes: " + " → ".join(path["modes"]) +
                                        f"\nFailed target: {path['failed_target']} · {path['hops']} hop(s)")
                else:
                    st.info(report["message"])
                if not report["source_graph_unchanged"]:
                    st.warning("Graph health counts changed during the simulation window; another operation may have modified the graph.")
                with st.expander("Simulation queries and trace"):
                    st.json(report["trace"])
                    st.caption(f"Source counts: {report['graph_health_before']} → {report['graph_health_after']}")
                    st.caption(report["classification_model"])
        except Exception as exc:
            st.error(f"Could not load or run the simulator: {type(exc).__name__}: {exc}")
        st.subheader("Subgraph Provenance")
        latest = st.session_state.get("last_result", {})
        evidence = latest.get("evidence", [])
        edges = []
        for row in evidence:
            nodes, types = row.get("nodes", []), row.get("edges", [])
            edges += [{"source": a, "relationship": rel, "target": b} for a, rel, b in zip(nodes, types, nodes[1:])]
        if edges:
            try:
                from streamlit_agraph import agraph, Node, Edge, Config
                node_names = sorted({v for edge in edges for v in (edge["source"], edge["target"])})
                agraph(nodes=[Node(id=name, label=name) for name in node_names],
                       edges=[Edge(source=e["source"], target=e["target"], label=e["relationship"]) for e in edges],
                       config=Config(width=650, height=350, directed=True, physics=True, nodeHighlightBehavior=True))
            except Exception as exc:
                st.dataframe(edges, width="stretch", hide_index=True)
                st.caption(f"Interactive graph viewer is unavailable ({type(exc).__name__}); showing the actual evidence edges as a table.")
        else:
            st.info("Ask a question to populate the viewer with actual returned evidence paths.")
        st.subheader("Curator Agent")
        if st.button("Run Curator Agent (Self-Heal Graph)", disabled=graph is None):
            try:
                st.session_state.curator_report = run_curator(graph, tenant_id)
            except Exception as exc:
                st.error(f"Curator run failed: {type(exc).__name__}: {exc}")
        if "curator_report" in st.session_state:
            st.json(st.session_state.curator_report)
        st.subheader("Recent Graph Episodes")
        try:
            rows = query_rows(graph, "MATCH (m:MemoryEpisode {tenant_id:$tenant}) RETURN m.session_id,m.created_at,m.summary ORDER BY m.created_at DESC LIMIT 10", {"tenant":tenant_id}, read_only=True)
            st.dataframe([{"session":r[0], "created_at":r[1], "summary":r[2]} for r in rows], width="stretch", hide_index=True)
        except Exception as exc:
            st.caption(f"No episode records could be read: {type(exc).__name__}: {exc}")

if client is not None:
    # Streamlit reruns are short lived; close the client each run.
    close_client(client)
