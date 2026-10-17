export type SourceOption = { tool: string; name: string; input_kind: "query" | "username" | "domain_limit" | "dns"; available: boolean; paid: boolean };
export type EvidenceRecord = {
  evidence_id: string; source: string; inputs: Record<string, unknown>; observed_at: string | null; retrieved_at: string | null;
  reused: boolean; links: { url: string; field_paths: string[] }[]; sha256: string; digest_recorded: boolean; integrity_matches: boolean | null;
  included_in_assessment: boolean; added_after_assessment: boolean;
  graph_references: { node_id: string; title: string; state: string }[];
  comparison: { previous_evidence_id: string; changed: boolean; added_urls: string[]; removed_urls: string[] } | null;
  observations: { label: string; value: string | number | boolean; field_path: string }[];
};
export type EvidenceLedger = {
  session_id: string; target: string; active: boolean; mode: "sources" | "research"; records: EvidenceRecord[];
  source_activity: { tool: string; attempts: number; captured: number; failed: number; unfinished: number; reused: number; not_captured: number }[];
  summary: { evidence_records: number; source_tools_with_evidence: number; unique_source_urls: number; reused_records: number; failed_attempts: number; unfinished_attempts: number; added_after_assessment: number };
  assessment: { present: boolean; committed_at: number | null; summary: string | null };
  limitations: string[];
};
