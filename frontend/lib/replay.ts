import type { ArcEvent } from "./investigation";

export type ReplayBundle = {
  version: 1;
  offline: true;
  events: ArcEvent[];
  token_report: { max_packet_bytes: number; raw_cached_data_bytes: number; measurement: string };
};
const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid replay object.");
  return value as Record<string, unknown>;
};
const text = (value: unknown) => { if (typeof value !== "string") throw new Error("Invalid replay text field."); };
const number = (value: unknown) => { if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw new Error("Invalid replay numeric field."); };
const array = (value: unknown): unknown[] => { if (!Array.isArray(value)) throw new Error("Invalid replay list."); return value; };
const texts = (value: unknown) => array(value).forEach(text);

export function parseReplayBundle(value: unknown): ReplayBundle {
  const data = object(value);
  if (data.version !== 1 || data.offline !== true) throw new Error("Choose an ARC offline replay.json file.");
  const report = object(data.token_report);
  number(report.max_packet_bytes); number(report.raw_cached_data_bytes); text(report.measurement);
  const events = array(data.events);
  if (events.length > 10000) throw new Error("Replay contains too many events.");
  for (const item of events) {
    const event = object(item); text(event.type);
    for (const key of ["session_id", "task_id", "goal", "role", "status", "state", "strategy", "reason", "support", "statement", "text", "phase", "stage", "error", "hypothesis_id", "lead_id", "url", "id", "summary", "archetype"]) {
      if (event[key] !== undefined) text(event[key]);
    }
    if (event.parent_id !== undefined && event.parent_id !== null) text(event.parent_id);
    for (const key of ["sequence", "timestamp", "wave", "max_waves", "depth", "token_upper_bound", "included", "excluded", "cache_read_input_tokens"]) {
      if (event[key] !== undefined) number(event[key]);
    }
    if (event.cost_usd !== undefined && event.cost_usd !== null) number(event.cost_usd);
    if (event.simulated !== undefined && typeof event.simulated !== "boolean") throw new Error("Invalid replay simulation flag.");
    if (event.unresolved !== undefined) texts(event.unresolved);
    if (event.missing_portrait_sections !== undefined) texts(event.missing_portrait_sections);
    if (event.coverage !== undefined) for (const value of array(event.coverage)) {
      const claim = object(value);
      for (const key of ["hypothesis_id", "statement", "support", "lifecycle", "rationale"]) text(claim[key]);
      if (typeof claim.insufficient_evidence !== "boolean") throw new Error("Invalid claim coverage.");
    }
    if (event.graph !== undefined) {
      const graph = object(event.graph); const ids = new Set<string>();
      for (const value of array(graph.nodes)) {
        const node = object(value);
        for (const key of ["node_id", "title", "label", "summary"]) text(node[key]);
        if (ids.has(node.node_id as string)) throw new Error("Duplicate graph node.");
        ids.add(node.node_id as string);
        for (const key of ["url", "evidence_state"]) if (node[key] !== undefined) text(node[key]);
        if (node.evidence_ids !== undefined) texts(node.evidence_ids);
        if (node.confidence !== undefined && node.confidence !== null) number(node.confidence);
      }
      for (const value of array(graph.edges)) {
        const edge = object(value);
        for (const key of ["source_id", "target_id", "relationship", "reason"]) text(edge[key]);
        if (!ids.has(edge.source_id as string) || !ids.has(edge.target_id as string)) throw new Error("Graph edge has an unknown endpoint.");
      }
      if (graph.portrait !== undefined) for (const value of array(graph.portrait)) {
        const section = object(value);
        for (const key of ["section", "title", "analysis", "limitations"]) text(section[key]);
        texts(section.evidence_ids);
      }
    }
  }
  return data as ReplayBundle;
}
