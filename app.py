"""Streamlit dashboard for HashiraGraph.

Enterprise knowledge, service dependencies, and persistent agent memory on FalkorDB.
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from action_agent import ActionAgent, persist_episode
from curator_agent import run_curator
from graph_store import close_client, connect_graph, graph_health, query_rows
from insights import fragility_ranking
from seed_graph import seed_enterprise_graph
from simulation import list_services, simulate_failure
import gemini_features as gf

# -----------------------------------------------------------------------------
# Configuration & Page Setup
# -----------------------------------------------------------------------------

TENANTS = {
    "hashira_demo": "Hashira demo",
    "demo_acme": "Synthetic Acme tenant",
}

st.set_page_config(
    page_title="HashiraGraph",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------------------
# Restrained, Minimal Light Design System
# -----------------------------------------------------------------------------

CUSTOM_CSS = """
<style>
:root {
    --bg-page: #f8fafc;
    --bg-surface: #ffffff;
    --text-primary: #0f172a;
    --text-secondary: #475569;
    --text-muted: #64748b;
    --border-subtle: #e2e8f0;
    --border-strong: #cbd5e1;
    --accent: #1d4ed8;
    --accent-hover: #1e40af;
    --accent-subtle: #eff6ff;
}

html, body, [class*="css"], .stMarkdown, .stText {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
    color: var(--text-primary);
}

.main .block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 2.5rem !important;
    max-width: 1440px !important;
}

/* Header */
.app-header {
    border-bottom: 1px solid var(--border-subtle);
    padding-bottom: 0.875rem;
    margin-bottom: 1rem;
}
.app-header h1 {
    font-size: 1.25rem !important;
    font-weight: 600 !important;
    color: var(--text-primary) !important;
    margin: 0 !important;
    letter-spacing: -0.01em;
    line-height: 1.2;
}
.app-header p {
    font-size: 0.8125rem !important;
    color: var(--text-secondary) !important;
    margin: 0.25rem 0 0 0 !important;
}

/* Section Headings */
.section-title {
    font-size: 0.875rem !important;
    font-weight: 600 !important;
    color: var(--text-primary) !important;
    margin-bottom: 0.25rem !important;
    letter-spacing: -0.01em;
}
.section-desc {
    font-size: 0.78125rem !important;
    color: var(--text-secondary) !important;
    margin-bottom: 0.75rem !important;
    line-height: 1.4;
}

/* Metric Cards */
[data-testid="stMetric"] {
    background-color: var(--bg-surface) !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: 4px !important;
    padding: 0.625rem 0.875rem !important;
    box-shadow: none !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.6875rem !important;
    font-weight: 600 !important;
    color: var(--text-muted) !important;
    text-transform: uppercase !important;
    letter-spacing: 0.04em !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.25rem !important;
    font-weight: 600 !important;
    color: var(--text-primary) !important;
}

/* Controls & Buttons */
.stButton > button {
    border-radius: 4px !important;
    font-size: 0.8125rem !important;
    font-weight: 500 !important;
    border: 1px solid var(--border-strong) !important;
    background-color: var(--bg-surface) !important;
    color: var(--text-primary) !important;
    padding: 0.375rem 0.75rem !important;
    box-shadow: none !important;
    transition: background-color 0.15s ease, border-color 0.15s ease !important;
}
.stButton > button:hover {
    background-color: #f1f5f9 !important;
    border-color: #94a3b8 !important;
    color: var(--text-primary) !important;
    transform: none !important;
    box-shadow: none !important;
}
.stButton > button[kind="primary"] {
    background-color: var(--accent) !important;
    color: #ffffff !important;
    border: 1px solid var(--accent) !important;
}
.stButton > button[kind="primary"]:hover {
    background-color: var(--accent-hover) !important;
    border-color: var(--accent-hover) !important;
    color: #ffffff !important;
    transform: none !important;
    box-shadow: none !important;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.5rem !important;
    border-bottom: 1px solid var(--border-subtle) !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 0 !important;
    padding: 0.4rem 0.6rem !important;
    font-size: 0.8125rem !important;
    font-weight: 500 !important;
    color: var(--text-secondary) !important;
    border-bottom: 2px solid transparent !important;
    background: transparent !important;
}
.stTabs [aria-selected="true"] {
    color: var(--accent) !important;
    border-bottom: 2px solid var(--accent) !important;
    font-weight: 600 !important;
}

/* Callouts */
.callout-info {
    background-color: #f8fafc;
    border: 1px solid var(--border-subtle);
    border-left: 3px solid var(--accent);
    padding: 0.625rem 0.875rem;
    font-size: 0.8125rem;
    color: var(--text-primary);
    border-radius: 0 4px 4px 0;
    margin: 0.75rem 0;
}
.callout-warning {
    background-color: #fffbeb;
    border: 1px solid #fef3c7;
    border-left: 3px solid #d97706;
    padding: 0.625rem 0.875rem;
    font-size: 0.8125rem;
    color: #92400e;
    border-radius: 0 4px 4px 0;
    margin: 0.75rem 0;
}

/* Dataframe & Expanders */
.stDataFrame {
    width: 100% !important;
    border: 1px solid var(--border-subtle) !important;
    border-radius: 4px !important;
}
[data-testid="stExpander"] {
    border: 1px solid var(--border-subtle) !important;
    border-radius: 4px !important;
    box-shadow: none !important;
    margin-top: 0.5rem !important;
    margin-bottom: 0.5rem !important;
}

/* Status Badges */
.status-tag {
    display: inline-block;
    padding: 0.125rem 0.45rem;
    border-radius: 3px;
    font-size: 0.6875rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
}
.status-tag-active {
    background-color: #ecfdf5;
    color: #065f46;
    border: 1px solid #a7f3d0;
}
.status-tag-neutral {
    background-color: #f1f5f9;
    color: #475569;
    border: 1px solid #cbd5e1;
}

@media (max-width: 992px) {
    [data-testid="column"] {
        width: 100% !important;
        flex: 1 1 100% !important;
        margin-bottom: 1.25rem;
    }
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# Standalone Interactive Topology Generator & Popup Dialog
# -----------------------------------------------------------------------------

def generate_topology_html(edges: list[dict[str, str]], title: str = "HashiraGraph Topology") -> str:
    """Generate self-contained interactive Vis.js HTML document."""
    node_set = sorted({v for e in edges for v in (e["source"], e["target"])})
    sources = {e["source"] for e in edges}
    targets = {e["target"] for e in edges}

    vis_nodes = [
        {
            "id": name,
            "label": name,
            "shape": "box",
            "margin": 10,
            "color": {
                "background": "#ffffff",
                "border": "#1d4ed8" if (name in sources and name in targets) else ("#0f172a" if name in sources else "#475569"),
                "highlight": {"background": "#eff6ff", "border": "#1d4ed8"},
            },
            "font": {"size": 13, "color": "#0f172a", "face": "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif"},
            "borderWidth": 1.5,
        }
        for name in node_set
    ]

    vis_edges = [
        {
            "from": e["source"],
            "to": e["target"],
            "label": e["relationship"],
            "arrows": "to",
            "color": {"color": "#94a3b8", "highlight": "#1d4ed8"},
            "font": {"size": 10, "color": "#64748b", "align": "horizontal"},
            "smooth": {"type": "cubicBezier", "forceDirection": "horizontal"},
        }
        for e in edges
    ]

    html_code = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  body {{
    margin: 0;
    padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background-color: #f8fafc;
    color: #0f172a;
    overflow: hidden;
  }}
  .toolbar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 18px;
    background: #ffffff;
    border-bottom: 1px solid #e2e8f0;
  }}
  .toolbar-title {{
    font-size: 13.5px;
    font-weight: 600;
  }}
  .toolbar-sub {{
    font-size: 11px;
    color: #64748b;
  }}
  .toolbar-actions {{
    display: flex;
    gap: 8px;
  }}
  .btn {{
    font-size: 11px;
    font-weight: 500;
    padding: 4px 10px;
    border-radius: 4px;
    border: 1px solid #cbd5e1;
    background: #ffffff;
    cursor: pointer;
  }}
  .btn:hover {{
    background: #f1f5f9;
  }}
  #mynetwork {{
    width: 100vw;
    height: calc(100vh - 54px);
    background: #f8fafc;
  }}
</style>
</head>
<body>
<div class="toolbar">
  <div>
    <div class="toolbar-title">{title}</div>
    <div class="toolbar-sub">{len(node_set)} entities, {len(edges)} directed dependency links</div>
  </div>
  <div class="toolbar-actions">
    <button class="btn" onclick="network.fit()">Fit View</button>
    <button class="btn" onclick="togglePhysics()">Toggle Physics</button>
  </div>
</div>
<div id="mynetwork"></div>
<script type="text/javascript">
  const container = document.getElementById('mynetwork');
  const data = {{
    nodes: new vis.DataSet({json.dumps(vis_nodes)}),
    edges: new vis.DataSet({json.dumps(vis_edges)})
  }};
  let physicsEnabled = true;
  const options = {{
    physics: {{
      stabilization: true,
      barnesHut: {{
        gravitationalConstant: -2500,
        springLength: 100,
        springConstant: 0.04
      }}
    }},
    interaction: {{
      hover: true,
      zoomView: true,
      dragView: true
    }}
  }};
  const network = new vis.Network(container, data, options);
  function togglePhysics() {{
    physicsEnabled = !physicsEnabled;
    network.setOptions({{ physics: {{ enabled: physicsEnabled }} }});
  }}
</script>
</body>
</html>"""
    return html_code


@st.dialog("Graph Topology Viewer", width="large")
def show_topology_modal(edges: list[dict[str, str]], title: str = "Graph Topology"):
    """Render full interactive graph topology in a focused popup dialog modal."""
    if not edges:
        st.info("No topology connections to display.")
        return

    node_set = sorted({v for e in edges for v in (e["source"], e["target"])})
    st.caption(f"{len(node_set)} entities, {len(edges)} directed relationship links.")

    html = generate_topology_html(edges, title)
    b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
    data_uri = f"data:text/html;base64,{b64}"

    col_btn1, col_btn2 = st.columns([1, 1])
    with col_btn1:
        st.markdown(
            f'<a href="{data_uri}" target="_blank" style="display: inline-block; padding: 0.375rem 0.75rem; font-size: 0.8125rem; font-weight: 500; text-decoration: none; color: #1d4ed8; background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 4px;">Open in Separate Window / Tab</a>',
            unsafe_allow_html=True,
        )
    with col_btn2:
        st.download_button(
            "Download Standalone HTML",
            data=html,
            file_name="graph_topology.html",
            mime="text/html",
            key="dl_modal_html",
        )

    components.html(html, height=520, scrolling=False)

    with st.expander("Structured Edge Data", expanded=False):
        st.dataframe(edges, width="stretch", hide_index=True)


# -----------------------------------------------------------------------------
# State Initialization
# -----------------------------------------------------------------------------

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
if "messages" not in st.session_state:
    st.session_state.messages = []

# -----------------------------------------------------------------------------
# Sidebar: Scope & Controls
# -----------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Workspace Scope")
    tenant_id = st.selectbox(
        "Authorized Tenant",
        list(TENANTS),
        format_func=lambda key: TENANTS[key],
        help="Fixed allowlisted demo graphs. Logical separation for demo isolation.",
    )

    st.caption(
        "Graph-name separation is a demo safeguard, not a production authentication boundary."
    )

    st.divider()

    st.markdown("### Session Management")
    short_sess = st.session_state.session_id[:8]
    st.text(f"Session: {short_sess}")
    if st.button("New Session", help="Start a clean session context"):
        st.session_state.session_id = uuid.uuid4().hex
        st.session_state.messages = []
        st.rerun()

    if st.button("Seed Enterprise Graph (90+ Nodes)", help="Seed or refresh 25 services, 11 teams, and multi-hop dependencies"):
        if graph is not None:
            seed_enterprise_graph(graph)
            st.success("Successfully seeded 90+ enterprise nodes and 100+ relations.")
            st.rerun()

    st.divider()

    st.markdown("### Execution Engine")
    fast_mode = st.toggle(
        "Sub-50ms Fast Engine",
        value=True,
        help="Deterministic algorithmic routing, O(1) greeting dispatch, and instant grounded synthesis.",
    )
    if fast_mode:
        st.markdown(
            '<span class="status-tag status-tag-active">Deterministic Fast-Path Active</span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<span class="status-tag status-tag-neutral">LLM Synthesis Active</span>',
            unsafe_allow_html=True,
        )

    st.caption(
        "All graph operations run via fixed, parameterized Cypher allowlists."
    )

    st.divider()

    st.markdown("### AI & Gemini Services")
    if gf.is_gemini_available():
        st.markdown('<span class="status-tag status-tag-active">Gemini 3.8 Flash Connected</span>', unsafe_allow_html=True)
        st.caption("Incident Playbooks, Architecture Briefs, and ADR Drafting enabled.")
    else:
        st.markdown('<span class="status-tag status-tag-neutral">Gemini Unconfigured</span>', unsafe_allow_html=True)
        st.caption("Add GEMINI_API_KEY in .env to enable AI Playbooks and ADRs.")

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
    st.error(f"FalkorDB Unavailable: {type(exc).__name__}: {exc}")
    st.info("Check connection credentials in .env. The system operates directly against FalkorDB.")

# -----------------------------------------------------------------------------
# Header & High-Level Health Metrics
# -----------------------------------------------------------------------------

st.markdown(
    """
    <div class="app-header">
        <h1>HashiraGraph</h1>
        <p>Enterprise service dependency intelligence, failure simulation, and persistent memory.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if graph is not None:
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    with m_col1:
        st.metric("Tenant", TENANTS.get(tenant_id, tenant_id))
    with m_col2:
        st.metric("Graph Nodes", health["nodes"], help="Total knowledge nodes (services, teams, people, incidents, decisions)")
    with m_col3:
        st.metric("Relationships", health["relationships"], help="Total connections and dependency links")
    with m_col4:
        st.metric("Status", "Operational", help=f"Active FalkorDB Graph: {graph.name}")

# -----------------------------------------------------------------------------
# Main Application Workspace
# -----------------------------------------------------------------------------

col_sim, col_agent = st.columns([1, 1], gap="large")

# =============================================================================
# COLUMN 1: Primary Task - Failure Impact Simulator & System Fragility
# =============================================================================

with col_sim:
    st.markdown('<div class="section-title">Failure Impact Simulator</div>', unsafe_allow_html=True)
    st.caption("Evaluate multi-hop dependency failure propagation without modifying stored relationships.")

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
                st.markdown(f"**Simulated targets:** `{', '.join(failed_names)}`")

                affected = report.get("affected", [])
                if affected:
                    down_count = sum(1 for r in affected if r["status"] == "DOWN")
                    deg_count = sum(1 for r in affected if r["status"] == "DEGRADED")

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Total Affected", len(affected))
                    c2.metric("DOWN (Hard Path)", down_count)
                    c3.metric("DEGRADED (Soft Path)", deg_count)

                    st.markdown(
                        f'<div class="callout-warning">'
                        f'<strong>Impact summary:</strong> Failure of {", ".join(failed_names)} affects '
                        f'{len(affected)} downstream service(s) ({down_count} down, {deg_count} degraded).'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    display_rows = [
                        {
                            "Service": r["service"],
                            "Status": r["status"],
                            "Criticality": r["criticality"] or "normal",
                            "Owners": ", ".join(r["owners"]) or "unassigned",
                            "Hops": r["hops"],
                            "Failed Target": r["failed_target"],
                            "Evidence Path": " -> ".join(r["path"]),
                            "Modes": " -> ".join(r["modes"]),
                        }
                        for r in affected
                    ]
                    st.dataframe(display_rows, width="stretch", hide_index=True)

                    with st.expander("Evidence Paths", expanded=False):
                        for r in affected:
                            st.markdown(f"**{r['service']}** ({r['status']})")
                            for path in r.get("evidence_paths", []):
                                st.code(
                                    f"Path: {' -> '.join(path['path'])}\n"
                                    f"Modes: {' -> '.join(path['modes'])}\n"
                                    f"Via target: {path['failed_target']} ({path['hops']} hop{'s' if path['hops'] != 1 else ''})"
                                )
                else:
                    st.info(report.get("message", "No upstream services are impacted by the selected targets."))

                if not report["source_graph_unchanged"]:
                    st.warning("Graph counts changed during simulation window (concurrent write detected).")

                with st.expander("Simulation Query and Diagnostic Trace", expanded=False):
                    st.caption(f"Health Before: {report['graph_health_before']} | After: {report['graph_health_after']}")
                    st.caption(f"Classification Model: {report['classification_model']}")
                    st.json(report["trace"])

                # Actions: Save to memory and view in popup modal
                sim_edges = []
                for aff in affected:
                    for ep in aff.get("evidence_paths", []):
                        p_nodes = ep.get("path", [])
                        p_modes = ep.get("modes", [])
                        for a, rel, b in zip(p_nodes, p_modes, p_nodes[1:]):
                            sim_edges.append({"source": a, "relationship": rel, "target": b})

                col_save, col_sim_top = st.columns([1.3, 1.1])
                with col_save:
                    if st.button("Save this simulation to persistent memory", key=f"save_sim_{tenant_id}"):
                        names = ", ".join(row["service"] for row in report["failed_targets"])
                        summary = "; ".join(f"{a['service']} {a['status']}" for a in report["affected"]) or "no dependent services impacted"
                        episode_id = persist_episode(
                            graph,
                            tenant_id,
                            st.session_state.session_id,
                            f"Simulated failure of {names}",
                            f"Simulation result: {summary}",
                            [{"service": a["service"], "status": a["status"]} for a in report["affected"]],
                            source="What-If Simulator",
                            kind="simulation",
                        )
                        st.success(f"Saved simulation episode {episode_id[:8]}.")
                with col_sim_top:
                    if sim_edges and st.button("View Simulation Topology", key=f"sim_top_{tenant_id}"):
                        show_topology_modal(sim_edges, f"Outage Topology - {', '.join(failed_names)}")

                if affected:
                    st.markdown("---")
                    st.markdown('<div class="section-title">Gemini SRE Incident Playbook</div>', unsafe_allow_html=True)
                    st.caption("Generate an actionable on-call remediation and architectural mitigation plan.")

                    if st.button("Generate Incident Playbook", key=f"gemini_playbook_{tenant_id}"):
                        with st.spinner("Analyzing failure cascade with Gemini..."):
                            playbook = gf.generate_incident_playbook(failed_names, affected)
                            st.session_state.current_playbook = playbook
                            st.session_state.current_playbook_targets = failed_names

                    if st.session_state.get("current_playbook") and st.session_state.get("current_playbook_targets") == failed_names:
                        st.markdown(st.session_state.current_playbook)
                        if st.button("Save Playbook to Persistent Memory", key=f"save_playbook_{tenant_id}"):
                            ep_id = persist_episode(
                                graph,
                                tenant_id,
                                st.session_state.session_id,
                                f"Incident playbook for {', '.join(failed_names)} outage",
                                st.session_state.current_playbook[:400],
                                [{"service": a["service"], "status": a["status"]} for a in affected],
                                source="Gemini Incident Playbook",
                                kind="playbook",
                            )
                            st.success(f"Saved incident playbook to persistent memory ({ep_id[:8]}).")

            st.divider()
            st.markdown('<div class="section-title">System Fragility Ranking</div>', unsafe_allow_html=True)
            st.caption("Rank services by cumulative downstream impact and network betweenness.")
            if st.button("Compute fragility ranking", key=f"fragility_{tenant_id}"):
                st.session_state.fragility_report = fragility_ranking(graph)
                st.session_state.fragility_tenant = tenant_id

            frag = st.session_state.get("fragility_report")
            if frag and st.session_state.get("fragility_tenant") == tenant_id:
                st.dataframe(
                    [
                        {
                            "Service": r["service"],
                            "Criticality": r["criticality"],
                            "DOWN if lost": r["hard_dependents_down"],
                            "DEGRADED if lost": r["soft_dependents_degraded"],
                            "Betweenness": r["betweenness"],
                            "Would go down": ", ".join(r["down_if_lost"]) or "-",
                        }
                        for r in frag["ranking"]
                    ],
                    width="stretch",
                    hide_index=True,
                )
                st.caption(f"{frag['method']} status: {frag['algorithm_status']}.")
        except Exception as exc:
            st.error(f"Simulator error: {type(exc).__name__}: {exc}")
    else:
        st.info("Connect to FalkorDB to run the failure simulator.")

# =============================================================================
# COLUMN 2: Knowledge Agent & Operations Console
# =============================================================================

with col_agent:
    st.markdown('<div class="section-title">Knowledge Agent</div>', unsafe_allow_html=True)
    st.caption("Ask questions about service ownership, dependencies, incidents, or saved notes.")

    # Sample query prompts
    st.caption("Sample queries:")
    q_col1, q_col2, q_col3 = st.columns(3)
    clicked_prompt = None
    with q_col1:
        if st.button("Who owns Payment API?", key="qp_1", use_container_width=True):
            clicked_prompt = "Who owns Payment API?"
    with q_col2:
        if st.button("Who owns Inventory?", key="qp_2", use_container_width=True):
            clicked_prompt = "Who owns Inventory Service?"
    with q_col3:
        if st.button("Payment failure impact", key="qp_3", use_container_width=True):
            clicked_prompt = "If the Payment API fails, which upstream services may be affected, and which teams own them?"

    q_col4, q_col5, q_col6 = st.columns(3)
    with q_col4:
        if st.button("Recent incidents", key="qp_4", use_container_width=True):
            clicked_prompt = "Which incidents affected Payment API?"
    with q_col5:
        if st.button("Save project note", key="qp_5", use_container_width=True):
            clicked_prompt = "Remember this note: Our team focuses on transparent dependency graph intelligence."
    with q_col6:
        if st.button("Recall saved notes", key="qp_6", use_container_width=True):
            clicked_prompt = "What did I tell you about our team?"

    # Message feed
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    prompt = st.chat_input(
        "Ask about services, dependencies, incidents, or team ownership...",
        disabled=graph is None,
    )

    effective_prompt = prompt or clicked_prompt

    if effective_prompt and graph is not None:
        st.session_state.messages.append({"role": "user", "content": effective_prompt})
        t_start = time.perf_counter()
        result = ActionAgent(graph, tenant_id, st.session_state.session_id, fast_mode=fast_mode).ask(effective_prompt)
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        result.setdefault("trace", {})["elapsed_ms"] = elapsed_ms
        st.session_state.last_result = result
        st.session_state.messages.append({"role": "assistant", "content": result["answer"]})

        if result.get("trace", {}).get("memory_write_error"):
            st.warning("The response completed, but the exchange could not be saved to persistent memory.")

        close_client(client)
        client = None
        st.rerun()

    # Query provenance for latest response
    if st.session_state.messages and "last_result" in st.session_state:
        last_msg = st.session_state.messages[-1]
        if last_msg["role"] == "assistant":
            result = st.session_state.last_result
            trace = result.get("trace", {})
            evidence = result.get("evidence", [])

            st.markdown('<div class="section-title" style="margin-top: 1rem;">Query Provenance and Evidence</div>', unsafe_allow_html=True)

            if "elapsed_ms" in trace:
                st.caption(f"Responded in {trace['elapsed_ms']:.1f} ms via {trace.get('tool', 'tool')}")

            if evidence:
                st.caption(f"Retrieved {len(evidence)} verified record(s):")
                if isinstance(evidence, list) and len(evidence) > 0 and isinstance(evidence[0], dict):
                    st.dataframe(evidence, width="stretch", hide_index=True)
                else:
                    st.json(evidence)
            else:
                st.info("No matching records returned from the selected graph.")

            with st.expander("Cypher Query Plan and Diagnostic Trace", expanded=False):
                if trace.get("error"):
                    st.error(f"Query Diagnostic: {trace['error']}")
                else:
                    st.write(
                        f"Executed tool `{trace.get('tool')}` against fixed allowlist. "
                        f"Returned {trace.get('rows', 0)} row(s)."
                    )
                st.code(trace.get("query") or "No direct query executed", language="cypher")
                if trace.get("verification"):
                    v = trace["verification"]
                    st.caption(f"Grounding check: {v['status']}" + (f" - unsupported: {', '.join(v['unsupported'])}" if v.get("unsupported") else ""))
                for note in trace.get("notes", []):
                    st.caption(f"• {note}")

    st.divider()

    # Secondary inspection tabs
    tab_mem, tab_cur, tab_vis, tab_gemini = st.tabs([
        "Memory Episodes",
        "Curator Maintenance",
        "Graph Topology",
        "Gemini Intelligence",
    ])

    with tab_mem:
        st.markdown('<div class="section-title">Memory Episodes</div>', unsafe_allow_html=True)
        st.caption(f"Stored MemoryEpisode records for tenant {tenant_id} in FalkorDB.")

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
                            "Type": (r[2] or "exchange").capitalize(),
                            "Prompt": r[4] or "",
                            "Summary": r[3] or "",
                            "Services": ", ".join(r[5]) if r[5] else "-",
                            "Session": str(r[0])[:8] if r[0] else "",
                        }
                        for r in ep_rows
                    ]
                    st.dataframe(formatted_eps, width="stretch", hide_index=True)
                else:
                    st.info(f"No memory episodes found for tenant '{tenant_id}'.")
            except Exception as exc:
                st.caption(f"Could not read memory episodes: {type(exc).__name__}: {exc}")
        else:
            st.info("Database unavailable.")

    with tab_cur:
        st.markdown('<div class="section-title">Curator Maintenance</div>', unsafe_allow_html=True)
        st.caption("Verify schema consistency, promote generic relationships, and create audited rollups.")

        if st.button("Run Curator Maintenance", disabled=graph is None, key=f"curator_{tenant_id}"):
            try:
                st.session_state.curator_report = run_curator(graph, tenant_id)
            except Exception as exc:
                st.error(f"Curator run failed: {type(exc).__name__}: {exc}")

        if "curator_report" in st.session_state:
            rep = st.session_state.curator_report
            st.markdown("---")
            st.markdown("**Latest Audit Report**")
            c1, c2, c3 = st.columns(3)
            c1.metric("Generic Promoted", rep.get("generic_relationships_promoted", 0))
            c2.metric("Closed Ownerships", rep.get("ownership_facts_closed_with_explicit_ended_at", 0))
            c3.metric("Rollups Created", rep.get("memory_rollups_created", 0))

            with st.expander("Full Audit Report JSON", expanded=False):
                st.json(rep)

    with tab_vis:
        st.markdown('<div class="section-title">Graph Topology</div>', unsafe_allow_html=True)
        st.caption("Visual topology of entities and relationships in the active tenant graph.")

        # Extract edges from latest agent query
        latest = st.session_state.get("last_result", {})
        evidence = latest.get("evidence", [])
        raw_edges = []
        for row in evidence:
            nodes, types = row.get("nodes", []), row.get("edges", [])
            raw_edges += [{"source": a, "relationship": rel, "target": b} for a, rel, b in zip(nodes, types, nodes[1:])]

        # Extract edges from simulation report if chat evidence is empty
        if not raw_edges:
            sim_rep = st.session_state.get("simulation_report")
            if sim_rep and st.session_state.get("simulation_tenant") == tenant_id:
                for aff in sim_rep.get("affected", []):
                    for ep in aff.get("evidence_paths", []):
                        p_nodes = ep.get("path", [])
                        p_modes = ep.get("modes", [])
                        for a, rel, b in zip(p_nodes, p_modes, p_nodes[1:]):
                            raw_edges.append({"source": a, "relationship": rel, "target": b})

        # Deduplicate edges
        edges = []
        seen = set()
        for e in raw_edges:
            k = (e["source"], e["relationship"], e["target"])
            if k not in seen:
                seen.add(k)
                edges.append(e)

        # Allow user to load live tenant dependencies if neither chat nor simulation has run
        if not edges and graph is not None:
            if st.button("Load Tenant Service Dependencies", key=f"load_dep_{tenant_id}"):
                try:
                    dep_rows = query_rows(
                        graph,
                        "MATCH (s1:Service)-[r:DEPENDS_ON]->(s2:Service) "
                        "RETURN s1.name, type(r), s2.name LIMIT 35",
                        read_only=True,
                    )
                    edges = [{"source": r[0], "relationship": r[1], "target": r[2]} for r in dep_rows]
                    st.session_state.loaded_topology_edges = edges
                    st.rerun()
                except Exception as exc:
                    st.caption(f"Could not load dependencies: {exc}")

        if not edges and "loaded_topology_edges" in st.session_state:
            edges = st.session_state.loaded_topology_edges

        if edges:
            node_set = sorted({v for e in edges for v in (e["source"], e["target"])})
            st.caption(f"Displaying {len(node_set)} entities and {len(edges)} directed dependency links.")

            html = generate_topology_html(edges, f"HashiraGraph - {tenant_id}")
            b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
            data_uri = f"data:text/html;base64,{b64}"

            # Actions row: Popup modal button + Open in separate window/tab link + Download
            col_act1, col_act2, col_act3 = st.columns([1.2, 1.4, 1.0])
            with col_act1:
                if st.button("View in Popup Modal", key="btn_view_modal", type="primary"):
                    show_topology_modal(edges, f"HashiraGraph - {tenant_id}")
            with col_act2:
                st.markdown(
                    f'<div style="margin-top: 3px;">'
                    f'<a href="{data_uri}" target="_blank" style="display: inline-block; padding: 0.35rem 0.75rem; font-size: 0.8125rem; font-weight: 500; text-decoration: none; color: #1d4ed8; background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 4px;">Open in Separate Window / Tab</a>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with col_act3:
                st.download_button(
                    "Export HTML",
                    data=html,
                    file_name="topology.html",
                    mime="text/html",
                    key="dl_tab_html",
                )

            # Embedded interactive viewer in tab
            components.html(html, height=440, scrolling=False)

            with st.expander("Structured Edge List", expanded=False):
                st.dataframe(edges, width="stretch", hide_index=True)
        else:
            st.info("No topology paths loaded yet. Click 'Load Tenant Service Dependencies' above or execute an agent query to visualize.")

    with tab_gemini:
        st.markdown('<div class="section-title">Gemini Graph Intelligence</div>', unsafe_allow_html=True)
        st.caption("AI-assisted system resilience assessments and Architecture Decision Record (ADR) generation.")

        st.markdown("##### 1. Executive Resilience Assessment")
        st.caption("Synthesize graph metrics and fragility rankings into an executive-ready architectural health brief.")
        if st.button("Generate Architecture Brief", key=f"gen_brief_{tenant_id}"):
            with st.spinner("Synthesizing graph topology with Gemini..."):
                frag_data = st.session_state.get("fragility_report", {}).get("ranking", [])
                brief = gf.generate_architecture_brief(tenant_id, health["nodes"], health["relationships"], frag_data)
                st.session_state.architecture_brief = brief

        if "architecture_brief" in st.session_state:
            st.markdown(st.session_state.architecture_brief)
            if st.button("Save Brief to Memory", key=f"save_brief_{tenant_id}"):
                ep_id = persist_episode(
                    graph,
                    tenant_id,
                    st.session_state.session_id,
                    "Executive Architecture Resilience Brief",
                    st.session_state.architecture_brief[:300],
                    [],
                    source="Gemini Architecture Brief",
                    kind="architecture_brief",
                )
                st.success(f"Saved architecture brief ({ep_id[:8]}).")

        st.divider()

        st.markdown("##### 2. Architecture Decision Record (ADR) Assistant")
        st.caption("Draft production-grade ADRs governing service boundaries, decoupling, and risk mitigations.")
        svc_choices = (
            service_options
            if "service_options" in locals() and service_options
            else ["Payment API", "Checkout API", "Inventory Service", "Ledger Service"]
        )
        adr_service = st.selectbox(
            "Governed Service",
            svc_choices,
            key=f"adr_svc_{tenant_id}",
        )
        adr_proposal = st.text_input(
            "Proposed Architectural Change",
            value="Introduce asynchronous event-driven queue for payment processing",
            key=f"adr_prop_{tenant_id}",
        )
        if st.button("Draft Architecture Decision Record", key=f"gen_adr_{tenant_id}"):
            with st.spinner("Drafting ADR with Gemini..."):
                deps = []
                if graph is not None:
                    try:
                        d_rows = query_rows(
                            graph,
                            "MATCH (s:Service {name:$s})-[:DEPENDS_ON]->(target:Service) RETURN target.name",
                            {"s": adr_service},
                            read_only=True,
                        )
                        deps = [r[0] for r in d_rows]
                    except Exception:
                        pass
                adr_text = gf.generate_adr_draft(adr_service, adr_proposal, deps)
                st.session_state.current_adr = adr_text

        if "current_adr" in st.session_state:
            st.markdown(st.session_state.current_adr)
            if st.button("Save ADR to Persistent Memory", key=f"save_adr_{tenant_id}"):
                ep_id = persist_episode(
                    graph,
                    tenant_id,
                    st.session_state.session_id,
                    f"ADR: {adr_proposal} for {adr_service}",
                    st.session_state.current_adr[:350],
                    [{"service": adr_service, "status": "governed"}],
                    source="Gemini ADR Generator",
                    kind="adr",
                )
                st.success(f"Saved ADR to persistent memory ({ep_id[:8]}).")

# -----------------------------------------------------------------------------
# Clean Teardown
# -----------------------------------------------------------------------------

if client is not None:
    close_client(client)
