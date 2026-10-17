"""Streamlit dashboard for HashiraGraph.

Enterprise knowledge, service dependencies, and persistent agent memory on FalkorDB.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import streamlit as st

from action_agent import ActionAgent
from curator_agent import run_curator
from graph_store import close_client, connect_graph, graph_health, query_rows
from simulation import list_services, simulate_failure

# -----------------------------------------------------------------------------
# Configuration & Page Setup
# -----------------------------------------------------------------------------

TENANTS = {
    "hashira_demo": "Hashira demo",
    "demo_acme": "Synthetic Acme tenant",
}

st.set_page_config(
    page_title="HashiraGraph — Knowledge & Agent Memory",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------------------
# Calm, Intentional Custom Styling
# -----------------------------------------------------------------------------

CUSTOM_CSS = """
<style>
/* Modern, restrained typography & layout spacing */
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}

/* Page header styling */
.header-container {
    padding-bottom: 0.75rem;
    margin-bottom: 1rem;
    border-bottom: 1px solid rgba(128, 128, 128, 0.2);
}
.header-title {
    font-size: 1.55rem;
    font-weight: 650;
    letter-spacing: -0.02em;
    margin: 0;
    color: var(--text-color);
}
.header-subtitle {
    font-size: 0.88rem;
    color: var(--text-color);
    opacity: 0.75;
    margin-top: 0.2rem;
    margin-bottom: 0;
}

/* Live status pill indicators */
.status-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.2rem 0.6rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 500;
    background: rgba(128, 128, 128, 0.12);
    border: 1px solid rgba(128, 128, 128, 0.2);
    color: var(--text-color);
}
.status-pill-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background-color: #10b981;
}

/* Operational Badges for Failure Simulator */
.badge-down {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
    background-color: rgba(239, 68, 68, 0.15);
    color: #ef4444;
    border: 1px solid rgba(239, 68, 68, 0.3);
}
.badge-degraded {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
    background-color: rgba(245, 158, 11, 0.15);
    color: #f59e0b;
    border: 1px solid rgba(245, 158, 11, 0.3);
}

/* Clean panel wrappers */
.section-panel {
    border: 1px solid rgba(128, 128, 128, 0.18);
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1rem;
    background: rgba(128, 128, 128, 0.03);
}

/* Accessible, clean tab padding */
.stTabs [data-baseweb="tab-list"] {
    gap: 1.5rem;
    border-bottom: 1px solid rgba(128, 128, 128, 0.2);
    padding-bottom: 0.2rem;
}
.stTabs [data-baseweb="tab"] {
    height: auto;
    padding: 0.5rem 0.2rem;
    font-size: 0.9rem;
    font-weight: 500;
}

/* Responsive table & code blocks */
.stDataFrame {
    width: 100% !important;
    overflow-x: auto;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# State Initialization
# -----------------------------------------------------------------------------

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
if "messages" not in st.session_state:
    st.session_state.messages = []

# -----------------------------------------------------------------------------
# Sidebar: Workspace & Tenant Scope
# -----------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Workspace Scope")
    tenant_id = st.selectbox(
        "Authorized Demo Tenant",
        list(TENANTS),
        format_func=lambda key: TENANTS[key],
        help="Fixed allowlisted demo graphs. Logical separation for demo isolation.",
    )

    st.caption(
        "Graph-name separation is a demo safeguard, not a production authentication boundary."
    )

    st.divider()

    st.markdown("### Session & Memory")
    short_sess = st.session_state.session_id[:8]
    st.text(f"Session ID: {short_sess}...")
    if st.button("New Session", help="Start a clean session context"):
        st.session_state.session_id = uuid.uuid4().hex
        st.session_state.messages = []
        st.rerun()

    st.divider()

    st.markdown("### System Architecture")
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        st.markdown(
            '<div class="status-pill"><span class="status-pill-dot"></span>Gemini Intent Wording Active</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="status-pill"><span class="status-pill-dot" style="background:#6b7280;"></span>Deterministic Tool Routing (Safe)</div>',
            unsafe_allow_html=True,
        )

    st.caption(
        "All graph operations run via fixed, parameterized Cypher allowlists. "
        "Gemini cannot generate or execute arbitrary queries."
    )

# -----------------------------------------------------------------------------
# Database Connection & Health Verification
# -----------------------------------------------------------------------------

client = graph = None
health = {"nodes": 0, "relationships": 0}

try:
    client, graph = connect_graph(tenant_id)
    health = graph_health(graph)
except Exception as exc:
    graph = None
    st.error(f"FalkorDB Cloud Unavailable: {type(exc).__name__}: {exc}")
    st.info("Check connection credentials in `.env`. The system operates directly against FalkorDB.")

# -----------------------------------------------------------------------------
# Calm Header with System Metrics
# -----------------------------------------------------------------------------

st.markdown(
    f"""
    <div class="header-container">
        <h1 class="header-title">HashiraGraph</h1>
        <p class="header-subtitle">
            Enterprise knowledge, service dependencies, and persistent agent memory on FalkorDB
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

if graph is not None:
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    with m_col1:
        st.metric("Tenant", TENANTS.get(tenant_id, tenant_id))
    with m_col2:
        st.metric("Graph Nodes", health["nodes"])
    with m_col3:
        st.metric("Relationships", health["relationships"])
    with m_col4:
        st.metric("Status", "Connected", help=f"Active FalkorDB Graph: {graph.name}")

# -----------------------------------------------------------------------------
# Main Application Layout: Two Balanced Workspaces
# -----------------------------------------------------------------------------

col_chat, col_tools = st.columns([1.1, 0.9], gap="large")

# =============================================================================
# LEFT COLUMN: Agent Console & Grounded Evidence
# =============================================================================

with col_chat:
    st.markdown("#### Agent Console")
    st.caption("Ask questions about service ownership, blast radius, incidents, decisions, or persistent memories.")

    # Render message history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat Input
    prompt = st.chat_input(
        "Ask a question or enter 'Remember this note: ...'",
        disabled=graph is None,
    )

    if prompt and graph is not None:
        st.session_state.messages.append({"role": "user", "content": prompt})
        result = ActionAgent(graph, tenant_id, st.session_state.session_id).ask(prompt)
        st.session_state.last_result = result
        st.session_state.messages.append({"role": "assistant", "content": result["answer"]})

        if result.get("trace", {}).get("memory_write_error"):
            st.warning("The response completed, but the exchange could not be saved to persistent memory.")

        # Explicit rerun skips the script's trailing cleanup, so close client first.
        close_client(client)
        client = None
        st.rerun()

    # Detailed Evidence & Audit Trace for the Most Recent Assistant Answer
    if st.session_state.messages and "last_result" in st.session_state:
        last_msg = st.session_state.messages[-1]
        if last_msg["role"] == "assistant":
            result = st.session_state.last_result
            trace = result.get("trace", {})
            evidence = result.get("evidence", [])

            st.markdown("---")
            st.markdown("##### Grounded Evidence & Provenance")

            if evidence:
                st.caption(f"Retrieved {len(evidence)} record(s) via tool: `{trace.get('tool', 'unknown')}`")
                # Format tabular summary if evidence has standard keys
                if isinstance(evidence, list) and len(evidence) > 0 and isinstance(evidence[0], dict):
                    st.dataframe(evidence, width="stretch", hide_index=True)
                else:
                    st.json(evidence)
            else:
                st.info("No matching records returned from the selected graph.")

            with st.expander("Cypher Query Plan & Self-Healing Trace", expanded=False):
                if trace.get("error"):
                    st.error(f"Query Diagnostic: {trace['error']}")
                else:
                    st.write(
                        f"Executed tool `{trace.get('tool')}` against fixed allowlist. "
                        f"Returned {trace.get('rows', 0)} row(s)."
                    )
                st.code(trace.get("query") or "No direct query executed", language="cypher")
                for note in trace.get("notes", []):
                    st.caption(f"• {note}")

# =============================================================================
# RIGHT COLUMN: Operations & Analysis Console (Organized into Clean Tabs)
# =============================================================================

with col_tools:
    tab_sim, tab_mem, tab_cur, tab_vis = st.tabs([
        "What-If Simulator",
        "Persistent Memory",
        "Curator Agent",
        "Subgraph Viewer",
    ])

    # -------------------------------------------------------------------------
    # TAB 1: What-If Failure Simulator
    # -------------------------------------------------------------------------
    with tab_sim:
        st.markdown("##### Counterfactual Failure Simulator")
        st.caption(
            "Bounded 4-hop read-only dependency traversal. "
            "Evaluates impact propagation without modifying stored relationships."
        )

        if graph is not None:
            try:
                service_options = list_services(graph)
                chosen_targets = st.multiselect(
                    "Select Target Service(s) to Fail",
                    service_options,
                    key=f"sim_targets_{tenant_id}",
                    placeholder="Choose services to simulate failure...",
                )

                if st.button("Simulate Impact", disabled=not chosen_targets, key=f"simulate_{tenant_id}", type="primary"):
                    st.session_state.simulation_report = simulate_failure(graph, chosen_targets)
                    st.session_state.simulation_tenant = tenant_id

                report = st.session_state.get("simulation_report")
                if report and st.session_state.get("simulation_tenant") == tenant_id:
                    st.markdown("---")
                    failed_names = [row["service"] for row in report["failed_targets"]]
                    st.markdown(f"**Simulated Targets:** `{', '.join(failed_names)}`")

                    affected = report.get("affected", [])
                    if affected:
                        down_count = sum(1 for r in affected if r["status"] == "DOWN")
                        deg_count = sum(1 for r in affected if r["status"] == "DEGRADED")

                        c1, c2, c3 = st.columns(3)
                        c1.metric("Total Affected", len(affected))
                        c2.metric("DOWN (Hard Path)", down_count)
                        c3.metric("DEGRADED (Soft Path)", deg_count)

                        # Clean impact table
                        display_rows = [
                            {
                                "Service": r["service"],
                                "Status": r["status"],
                                "Criticality": r["criticality"] or "unknown",
                                "Owners": ", ".join(r["owners"]) or "unassigned",
                                "Hops": r["hops"],
                                "Failed Target": r["failed_target"],
                                "Evidence Path": " → ".join(r["path"]),
                                "Modes": " → ".join(r["modes"]),
                            }
                            for r in affected
                        ]
                        st.dataframe(display_rows, width="stretch", hide_index=True)

                        with st.expander("Discovered Evidence Chains", expanded=False):
                            for r in affected:
                                st.markdown(f"**{r['service']}** (`{r['status']}`)")
                                for path in r.get("evidence_paths", []):
                                    st.code(
                                        f"Path: {' → '.join(path['path'])}\n"
                                        f"Modes: {' → '.join(path['modes'])}\n"
                                        f"Via target: {path['failed_target']} ({path['hops']} hop{'s' if path['hops'] != 1 else ''})"
                                    )
                    else:
                        st.info(report.get("message", "No upstream services are impacted by the selected targets."))

                    # Graph integrity indicator
                    if not report["source_graph_unchanged"]:
                        st.warning("Graph counts changed during simulation window (concurrent write detected).")

                    with st.expander("Simulation Cypher & Trace", expanded=False):
                        st.caption(f"Health Before: {report['graph_health_before']} | After: {report['graph_health_after']}")
                        st.caption(f"Classification Model: {report['classification_model']}")
                        st.json(report["trace"])

            except Exception as exc:
                st.error(f"Simulator error: {type(exc).__name__}: {exc}")
        else:
            st.info("Connect to FalkorDB to run the failure simulator.")

    # -------------------------------------------------------------------------
    # TAB 2: Persistent Memory Episodes
    # -------------------------------------------------------------------------
    with tab_mem:
        st.markdown("##### Persistent Memory Episodes")
        st.caption(
            f"Stored `MemoryEpisode` records for tenant `{tenant_id}` in FalkorDB. "
            "Supports tenant-scoped cross-session and current-session recall."
        )

        if graph is not None:
            try:
                ep_rows = query_rows(
                    graph,
                    "MATCH (m:MemoryEpisode {tenant_id:$tenant}) "
                    "OPTIONAL MATCH (m)-[:MENTIONS]->(s:Service) "
                    "RETURN m.session_id, m.created_at, m.kind, m.summary, m.user_message, collect(DISTINCT s.name) AS services "
                    "ORDER BY m.created_at DESC LIMIT 15",
                    {"tenant": tenant_id},
                    read_only=True,
                )
                if ep_rows:
                    formatted_eps = [
                        {
                            "Created": str(r[1])[:19] if r[1] else "",
                            "Kind": r[2] or "exchange",
                            "Prompt": r[4] or "",
                            "Summary / Answer": r[3] or "",
                            "Services": ", ".join(r[5]) if r[5] else "—",
                            "Session": str(r[0])[:8] if r[0] else "",
                        }
                        for r in ep_rows
                    ]
                    st.dataframe(formatted_eps, width="stretch", hide_index=True)
                else:
                    st.info(f"No memory episodes found yet for tenant '{tenant_id}'. Ask questions or save notes to create episodes.")
            except Exception as exc:
                st.caption(f"Could not read memory episodes: {type(exc).__name__}: {exc}")
        else:
            st.info("Database unavailable.")

    # -------------------------------------------------------------------------
    # TAB 3: Curator Agent (Self-Healing Graph Maintenance)
    # -------------------------------------------------------------------------
    with tab_cur:
        st.markdown("##### Curator Agent")
        st.caption(
            "Inspects schema consistency, promotes verified generic relationships, "
            "and creates audited memory rollups without deleting historical facts."
        )

        if st.button("Run Curator Maintenance", disabled=graph is None, key=f"curator_{tenant_id}"):
            try:
                st.session_state.curator_report = run_curator(graph, tenant_id)
            except Exception as exc:
                st.error(f"Curator run failed: {type(exc).__name__}: {exc}")

        if "curator_report" in st.session_state:
            rep = st.session_state.curator_report
            st.markdown("---")
            st.markdown("**Latest Curator Audit Report**")
            c1, c2, c3 = st.columns(3)
            c1.metric("Generic Promoted", rep.get("generic_relationships_promoted", 0))
            c2.metric("Closed Ownerships", rep.get("ownership_facts_closed_with_explicit_ended_at", 0))
            c3.metric("Rollups Created", rep.get("memory_rollups_created", 0))

            with st.expander("Full Audit Report JSON", expanded=False):
                st.json(rep)

    # -------------------------------------------------------------------------
    # TAB 4: Subgraph Viewer (Interactive Provenance)
    # -------------------------------------------------------------------------
    with tab_vis:
        st.markdown("##### Subgraph Provenance")
        st.caption("Visual topology of nodes and edges retrieved in the latest agent query.")

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
                agraph(
                    nodes=[Node(id=name, label=name) for name in node_names],
                    edges=[Edge(source=e["source"], target=e["target"], label=e["relationship"]) for e in edges],
                    config=Config(
                        width=600,
                        height=350,
                        directed=True,
                        physics=True,
                        nodeHighlightBehavior=True,
                    ),
                )
            except Exception as exc:
                st.dataframe(edges, width="stretch", hide_index=True)
                st.caption(f"Interactive graph rendered as structured table ({type(exc).__name__}).")
        else:
            st.info("Submit a blast radius or dependency query in Agent Chat to populate the graph viewer.")

# -----------------------------------------------------------------------------
# Clean Teardown
# -----------------------------------------------------------------------------

if client is not None:
    close_client(client)
