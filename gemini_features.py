"""Gemini-powered graph intelligence and analysis for HashiraGraph."""
from __future__ import annotations

import json
import os
from typing import Any

from graph_store import load_configuration


def get_gemini_client():
    """Retrieve an authenticated Google GenAI client if configured."""
    config = load_configuration()
    api_key = config.get("GEMINI_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None, None
    configured_model = config.get("GEMINI_MODEL", "").strip() or os.getenv("GEMINI_MODEL", "").strip()
    model = "gemini-2.5-flash" if (not configured_model or configured_model == "gemini-3.8-flash") else configured_model
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        return client, model
    except Exception:
        return None, None


def is_gemini_available() -> bool:
    """Check if Gemini API is configured and accessible."""
    client, _ = get_gemini_client()
    return client is not None


def _call_gemini_with_fallback(client, prompt: str, initial_model: str) -> str:
    """Attempt call across candidate models to bypass model-specific rate/quota limits."""
    candidates = [initial_model, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    seen = set()
    last_exc = None
    for m in candidates:
        if not m or m in seen:
            continue
        seen.add(m)
        try:
            resp = client.models.generate_content(model=m, contents=prompt)
            text = (resp.text or "").strip()
            if text:
                return text
        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            if "429" in err_str or "quota" in err_str or "resource_exhausted" in err_str:
                continue
            raise exc
    if last_exc:
        raise last_exc
    return ""


def _deterministic_incident_playbook(failed_targets: list[str], affected: list[dict[str, Any]]) -> str:
    targets_str = ", ".join(failed_targets) if failed_targets else "Target Service"
    affected_count = len(affected)
    tier1_affected = [a.get("service") for a in affected if str(a.get("tier", "")).lower() in ("tier 1", "1")]
    tier1_str = ", ".join(str(s) for s in tier1_affected[:4]) if tier1_affected else "No critical Tier 1 services directly broken"

    return f"""### Automated SRE Incident Remediation Playbook
*Simulated Outage Target: `{targets_str}` | Impact Radius: {affected_count} downstream dependencies*
*Generated via deterministic graph resilience engine (active SRE fallback).*

#### 1. Executive Impact Summary
Simulated failure of `{targets_str}` transitively propagates across **{affected_count} downstream dependencies**. Critical consumer-facing touchpoints at risk: **{tier1_str}**. Immediate triage is required to prevent customer transaction degradation.

#### 2. Immediate On-Call Incident Playbook
1. **Declare Severity & Open War-Room**: Notify on-call leads and incident bridge for `{targets_str}` and its immediate downstream dependents.
2. **Health Check & Process Isolation**: Inspect host metrics, error rates, and connection pool saturation on `{targets_str}`. Check pod health or restart worker pools if resource starvation is detected.
3. **Verify Ingress & Gateway Routing**: Check API gateway status codes (HTTP 502/504) to confirm whether degraded circuit breaking has engaged.

#### 3. Traffic Rerouting & Degraded Mode
- Activate edge circuit breakers to return cached responses or degraded static fallbacks for impacted endpoints.
- Route non-critical batch and analytics traffic away from the affected cluster to shed load.

#### 4. Long-Term Architectural Mitigations
- **Bulkhead Pattern**: Partition database connection pools and thread workers so `{targets_str}` outages cannot starve shared runtime resources.
- **Asynchronous Isolation**: Decouple synchronous REST/gRPC paths using event-driven pub/sub queues to buffer downstream failures during service interruptions.
"""


def _deterministic_architecture_brief(tenant_id: str, node_count: int, rel_count: int, fragility: list[dict[str, Any]]) -> str:
    top_fragile = fragility[:5] if fragility else []
    fragile_lines = []
    for f in top_fragile:
        svc = f.get("service", "Unknown")
        radius = f.get("blast_radius", f.get("impact", 0))
        tier = f.get("tier", "N/A")
        fragile_lines.append(f"- **{svc}** (Tier: {tier}, Downstream Blast Radius: {radius} services)")
    fragile_bullets = "\n".join(fragile_lines) if fragile_lines else "- No critical single points of failure detected."

    return f"""### Executive Architecture & Resilience Brief ({tenant_id})
*Generated via FalkorDB deterministic graph topology analysis engine.*

#### 1. System Topology Assessment
The enterprise graph currently models **{node_count} registered entities** interconnected by **{rel_count} directed dependency relationships**. The dependency distribution exhibits characteristic hub-and-spoke coupling around core foundational services.

#### 2. Key Single Points of Failure (Centrality & Blast Radius)
Analysis of multi-hop transitive paths reveals high operational fragility concentrated in top-tier dependencies:
{fragile_bullets}

These services represent high-leverage architectural nodes where localized failures cascade directly into customer-facing touchpoints.

#### 3. Decoupling & Mitigation Strategy
- **Asynchronous Isolation**: Decouple synchronous REST/gRPC paths using event-driven pub/sub messaging to buffer downstream failures.
- **Circuit Breakers & Bulkheads**: Implement resilient fallback caching at edge gateways so downstream unavailability degrades gracefully rather than triggering full outage cascades.
"""


def _deterministic_adr_draft(service: str, proposal: str, related_dependencies: list[str]) -> str:
    deps_str = ", ".join(related_dependencies) if related_dependencies else "None specified"
    return f"""### Architecture Decision Record: ADR-{service.replace(' ', '-').upper()}

- **Status**: Proposed
- **Target Service**: {service}
- **Connected Dependencies**: {deps_str}

#### Context
The service `{service}` operates within an interconnected dependency topology. Current architectural drivers require enhanced reliability, lower coupling, and bounded blast radius under failure conditions.

#### Decision
Implement: {proposal}.
Interface boundaries between `{service}` and its upstream/downstream dependencies ({deps_str}) will be formally decoupled using asynchronous messaging and circuit-breaking protocols.

#### Consequences
- **Positive**: Reduces transitive failure propagation, decouples deployment lifecycles, and bounds blast radius.
- **Negative**: Adds eventual consistency considerations and requires distributed tracing instrumentation.

#### Verification & Rollback Strategy
1. Deploy canary version with synthetic traffic mirroring.
2. Monitor latency percentiles (p95/p99) and error budgets.
3. Revert feature flags immediately if error rates exceed 0.1% threshold.
"""


def generate_incident_playbook(failed_targets: list[str], affected: list[dict[str, Any]]) -> str:
    """Generate an on-call incident remediation playbook and mitigation plan based on simulated failure propagation."""
    client, model = get_gemini_client()
    if not client:
        return _deterministic_incident_playbook(failed_targets, affected)

    prompt = f"""You are a Principal Site Reliability Engineer (SRE).
Analyze this simulated service outage from an enterprise dependency graph:

Failed Target Services: {', '.join(failed_targets)}
Impact Summary: {len(affected)} downstream services affected.

Downstream Dependencies Impacted:
{json.dumps(affected, indent=2, default=str)[:6000]}

Provide a concise, professional engineering response in standard markdown:
1. **Executive Impact Summary**: 2 sentences explaining customer impact.
2. **Immediate On-Call Incident Playbook**: 3 numbered triage steps for on-call engineers.
3. **Traffic Rerouting & Degraded Mode**: How dependent services should gracefully degrade (e.g. fallback caches, circuit breakers).
4. **Long-Term Architectural Recommendations**: 2 concrete architectural mitigations to eliminate this single point of failure (e.g. asynchronous queues, read replicas, bulkheads).

Keep it concise, technical, and actionable. Do not use emojis anywhere in your response.
"""
    try:
        return _call_gemini_with_fallback(client, prompt, model)
    except Exception:
        return _deterministic_incident_playbook(failed_targets, affected)


def generate_architecture_brief(tenant_id: str, node_count: int, rel_count: int, fragility: list[dict[str, Any]]) -> str:
    """Generate an executive architecture and resilience brief."""
    client, model = get_gemini_client()
    if not client:
        return _deterministic_architecture_brief(tenant_id, node_count, rel_count, fragility)

    top_fragile = fragility[:5] if fragility else []
    prompt = f"""You are a Principal Enterprise Systems Architect.
Review the structural topology of the company's dependency graph:

Tenant: {tenant_id}
Total Knowledge Entities: {node_count} nodes
Total Directed Relationships: {rel_count} relationships
Top Fragile Services (Ranked by Downstream Blast Radius & Centrality):
{json.dumps(top_fragile, indent=2, default=str)}

Provide a concise, high-impact Architecture Brief in markdown:
1. **System Topology Assessment**: Structural resilience observation.
2. **Key Single Points of Failure**: Why the top-ranked services present vulnerability.
3. **Decoupling Strategy**: Concrete recommendation to reduce coupling and blast radius.

Keep it strictly technical, objective, and executive-ready. Do not use emojis anywhere in your response.
"""
    try:
        return _call_gemini_with_fallback(client, prompt, model)
    except Exception:
        return _deterministic_architecture_brief(tenant_id, node_count, rel_count, fragility)


def generate_adr_draft(service: str, proposal: str, related_dependencies: list[str]) -> str:
    """Draft a production-grade Architecture Decision Record (ADR)."""
    client, model = get_gemini_client()
    if not client:
        return _deterministic_adr_draft(service, proposal, related_dependencies)

    prompt = f"""You are a Staff Software Architect.
Draft an Architecture Decision Record (ADR) according to standard industry structure for:

Service: {service}
Architectural Proposal: {proposal}
Related Connected Dependencies: {', '.join(related_dependencies) if related_dependencies else 'None specified'}

Structure the document as:
- **Title**: ADR-[Timestamp]: [Clear Title]
- **Status**: Proposed
- **Context**: Problem statement and architectural drivers.
- **Decision**: The chosen technical approach and interface boundaries.
- **Consequences**: Positive tradeoffs, negative tradeoffs, and operational risks.
- **Verification & Rollback Strategy**: How to validate in staging and rollback safely.

Do not use emojis anywhere in your response.
"""
    try:
        return _call_gemini_with_fallback(client, prompt, model)
    except Exception:
        return _deterministic_adr_draft(service, proposal, related_dependencies)
