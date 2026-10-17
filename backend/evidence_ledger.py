"""Deterministic research ledger. No model, provider or private-vault access."""

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from backend.tool_outputs import model_output


def content_digest(data) -> str:
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def source_links(data) -> list[dict]:
    links: dict[str, list[str]] = {}
    keys = {"url", "URL", "href", "html_url", "linkedinUrl", "url_user", "link", "source_url", "commit_url"}

    def walk(value, path="$"):
        if isinstance(value, dict):
            for key, item in value.items():
                field = f"{path}[{json.dumps(key)}]"
                if key in keys and isinstance(item, str):
                    try:
                        parsed = urlsplit(item)
                        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
                            links.setdefault(item, []).append(field)
                    except ValueError:
                        pass
                walk(item, field)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(data)
    return [{"url": url, "field_paths": paths} for url, paths in links.items()]


def source_observations(source: str, data) -> list[dict]:
    """Display only selected professional fields, verbatim and with payload paths."""
    fields = {"title": "Title", "headline": "Headline", "name": "Reported name", "login": "Account login",
              "description": "Description", "bio": "Public bio", "company": "Reported company", "site": "Reported site",
              "language": "Repository language", "email_domain": "Commit author domain", "published_at": "Publication date",
              "DOI": "DOI", "publisher": "Publisher", "given": "Author given name", "family": "Author family name",
              "ORCID": "Deposited ORCID", "captured_at": "Archive capture timestamp", "record_type": "DNS record type"}
    if source.endswith("inspect_domain_dns"):
        fields.update(data="DNS answer", TTL="Reported DNS TTL")
    if source.endswith("inspect_domain_theharvester"):
        fields["value"] = "Certificate hostname"
    if source.endswith("search_public_web"):
        fields["body"] = "Search snippet"
    observations = []

    def walk(value, path="$", inherited=None):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}[{json.dumps(key)}]", fields.get(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", inherited)
        elif inherited and value is not None and isinstance(value, (str, int, float, bool)):
            observations.append({"label": inherited, "value": value, "field_path": path})

    # LinkedIn's filtered view retains original profile paths under a wrapper.
    view = model_output(source, data)
    if source == "mcp__osint__scrape_linkedin_profile" and isinstance(view, dict) and "profile" in view:
        view = view["profile"]
    walk(view)
    return observations


def build_ledger(run) -> dict:
    from backend.source_cache import canonical_inputs
    evidence = run.scratchpad.raw_evidence if run.scratchpad else []
    starts = {e["tool_use_id"]: e for e in run.events if e["type"] == "tool.started" and e.get("tool_use_id")}
    ends = {e["tool_use_id"]: e for e in run.events if e["type"] in {"tool.finished", "tool.failed", "evidence.skipped"} and e.get("tool_use_id")}
    graph_event = next((e for e in reversed(run.events) if e["type"] == "graph.final_commit"), None)
    graph = graph_event.get("graph") if graph_event else None
    graph_ids = set()
    if graph:
        for item in [*graph.get("nodes", []), *graph.get("edges", []), *graph.get("portrait", [])]:
            graph_ids.update(item.get("evidence_ids", []))
    commits = [i for i, event in enumerate(run.events) if event["type"] == "graph.final_commit"]
    added_since = {event.get("evidence_id") for event in run.events[commits[-1] + 1:] if event["type"] == "graph.draft_node"} if commits else set()
    records, previous, groups = [], {}, {}
    for finding in evidence:
        data = finding["data"]
        digest = content_digest(data)
        links = source_links(data)
        signature = content_digest([finding["source"], canonical_inputs(finding["source"].split("__")[-1], finding.get("inputs", {}))])
        old = previous.get(signature)
        before = {link["url"] for link in source_links(old["data"])} if old else set()
        after = {link["url"] for link in links}
        references = [{"node_id": n["node_id"], "title": n["title"], "state": n.get("evidence_state", "unverified")}
                      for n in (graph or {}).get("nodes", []) if finding["evidence_id"] in n.get("evidence_ids", [])]
        records.append({
            "evidence_id": finding["evidence_id"], "source": finding["source"], "inputs": finding.get("inputs", {}),
            "observed_at": finding.get("observed_at"), "retrieved_at": finding.get("retrieved_at"),
            "reused": bool((finding.get("source_cache") or {}).get("hit")), "links": links,
            "observations": source_observations(finding["source"], data),
            "sha256": digest, "digest_recorded": bool(finding.get("content_sha256")),
            "integrity_matches": digest == finding["content_sha256"] if finding.get("content_sha256") else None,
            "graph_references": references, "included_in_assessment": finding["evidence_id"] in graph_ids,
            "added_after_assessment": finding["evidence_id"] in added_since,
            "comparison": {"previous_evidence_id": old["evidence_id"], "changed": digest != content_digest(old["data"]),
                           "added_urls": sorted(after - before), "removed_urls": sorted(before - after)} if old else None,
        })
        previous[signature] = finding
        groups.setdefault(finding["source"], {"tool": finding["source"], "attempts": 0, "captured": 0, "failed": 0, "unfinished": 0, "reused": 0, "not_captured": 0})
        groups[finding["source"]]["captured"] += 1
        groups[finding["source"]]["reused"] += int(bool((finding.get("source_cache") or {}).get("hit")))
    attempts = []
    for call_id, start in starts.items():
        tool = start["tool"]
        if not tool.startswith("mcp__"):
            continue
        end = ends.get(call_id)
        outcome = "failed" if end and end["type"] == "tool.failed" else (
            end.get("evidence_status", "skipped") if end else "interrupted" if run.done else "running")
        attempts.append({"tool_use_id": call_id, "tool": tool, "outcome": outcome,
                         "started_at": start.get("timestamp"), "finished_at": end.get("timestamp") if end else None,
                         "duration_seconds": max(0, end["timestamp"] - start["timestamp"]) if end and end.get("timestamp") and start.get("timestamp") else None})
        group = groups.setdefault(tool, {"tool": tool, "attempts": 0, "captured": 0, "failed": 0, "unfinished": 0, "reused": 0, "not_captured": 0})
        group["attempts"] += 1
        group["failed"] += int(outcome == "failed")
        group["unfinished"] += int(outcome in {"running", "interrupted"})
        group["not_captured"] += int(outcome in {"skipped", "empty", "not_evidence"})
    return {"session_id": run.scratchpad.session_id, "target": run.target, "active": not run.done,
            "mode": run.collection_mode, "records": records, "source_activity": list(groups.values()), "attempts": attempts,
            "summary": {"evidence_records": len(records), "source_tools_with_evidence": len({r["source"] for r in records}),
                        "unique_source_urls": len({link["url"] for r in records for link in r["links"]}),
                        "reused_records": sum(r["reused"] for r in records), "failed_attempts": sum(g["failed"] for g in groups.values()),
                        "unfinished_attempts": sum(g["unfinished"] for g in groups.values()),
                        "added_after_assessment": len(added_since - {None})},
            "assessment": {"present": graph is not None, "committed_at": graph_event.get("timestamp") if graph_event else None,
                           "summary": (graph or {}).get("summary"), "graph": graph},
            "limitations": ["Coverage describes saved records and attempted tools, not all possible sources or identity certainty.",
                            "Matching handles and author names are leads; operator selection is not independent verification.",
                            "SHA-256 covers the normalized captured payload; it does not certify the source's claims.",
                            "Reused snapshots retain their original collection dates. No freshness threshold is assumed."]}


def export_report(run) -> dict:
    ledger = build_ledger(run)
    return {"schema": "arc.evidence-report.v1", "exported_at": datetime.now(timezone.utc).isoformat(),
            **ledger, "evidence": run.scratchpad.raw_evidence,
            "operator_decisions": run.scratchpad.human_decisions}


def report_markdown(run) -> str:
    report = build_ledger(run)
    # JSON quoting keeps multiline/source-controlled strings visibly delimited.
    def quote(value):
        text = json.dumps(value, ensure_ascii=False).replace("`", "\\u0060").replace("<", "\\u003c").replace(">", "\\u003e")
        return f"`{text}`"
    lines = ["# ARC research report", "", f"Target: {quote(report['target'])}", "",
             "## Scope", "", *[f"- {item}" for item in report["limitations"]], "", "## Assessment", "",
             quote(report["assessment"]["summary"]) if report["assessment"]["present"] else "No model assessment has been committed.",
             "", f"Records added after assessment: {report['summary']['added_after_assessment']}", "", "## Source evidence", ""]
    for record in report["records"]:
        lines.extend([f"### {record['evidence_id']}", "", f"Source: {quote(record['source'])}",
                      f"Collected: {record['observed_at']} · Reused: {record['reused']}",
                      f"Payload SHA-256: {record['sha256']}",
                      f"Digest recorded at capture: {record['digest_recorded']} · Matches: {record['integrity_matches']}", ""])
        lines.extend(f"- Source URL: {quote(link['url'])}" for link in record["links"])
        for item in record["observations"]:
            lines.append(f"- {item['label']}: {quote(item['value'])} · Payload path: {quote(item['field_path'])}")
        lines.append("")
    graph = report["assessment"].get("graph") or {}
    if graph.get("nodes"):
        lines.extend(["## Assessed claims and identity links", ""])
        for node in graph["nodes"]:
            if node["label"] in {"Claim", "IdentityAnchor", "AIJudgment"}:
                lines.extend([f"### {quote(node['title'])}", "", f"Type: {node['label']} · Evidence state: {node['evidence_state']}",
                              quote(node.get("summary", "")), f"Evidence IDs: {quote(node.get('evidence_ids', []))}", ""])
    for section in graph.get("portrait", []):
        lines.extend([f"## Interpretation: {quote(section['title'])}", "", quote(section["analysis"]),
                      f"Limitations: {quote(section['limitations'])}", f"Evidence IDs: {quote(section['evidence_ids'])}", ""])
    lines.extend(["## Source outcomes", ""])
    for group in report["source_activity"]:
        lines.append(f"- {quote(group['tool'])}: {group['attempts']} attempts; {group['captured']} captured; {group['failed']} failed; {group['unfinished']} unfinished.")
    return "\n".join(lines) + "\n"
