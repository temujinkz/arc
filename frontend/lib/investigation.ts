export type Candidate = { label: string; description?: string };
export type HumanPrompt = {
  request_id: string;
  question: string;
  candidates: Candidate[];
  multi_select: boolean;
};
export type GraphNode = {
  id: string;
  title: string;
  label: string;
  summary?: string;
  confidence?: number | null;
  url?: string;
  evidence_ids?: string[];
  evidence_state?: string;
  isDraft?: boolean;
};
export type GraphLink = { source: string; target: string; relationship: string; reason?: string };
export type GraphData = { nodes: GraphNode[]; links: GraphLink[] };
export type PortraitSection = { section: string; title: string; analysis: string; evidence_ids: string[]; limitations: string };
export type GraphPlan = {
  nodes: (Omit<GraphNode, "id"> & { node_id: string })[];
  edges: { source_id: string; target_id: string; relationship: string; reason: string }[];
  portrait?: PortraitSection[];
};
export type ArcEvent = {
  type: string;
  session_id?: string;
  timestamp?: number;
  target?: string;
  model?: string;
  tool?: string;
  tool_use_id?: string;
  args?: Record<string, unknown>;
  subagent?: string;
  text?: string;
  preview?: string;
  error?: string;
  reason?: string;
  phase?: string;
  stage?: string;
  evidence_id?: string;
  evidence_status?: string;
  source_cache?: { hit: boolean; observed_at: string; key: string } | null;
  id?: string;
  source?: string;
  graph?: GraphPlan;
  request_id?: string;
  question?: string;
  candidates?: Candidate[];
  multi_select?: boolean;
  archetype?: string;
  score?: number | null;
  summary?: string;
  cost_usd?: number | null;
  mode?: "research" | "sources";
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
};
export type Status = "idle" | "starting" | "running" | "complete" | "failed" | "cancelled";
export type InvestigationState = {
  sessionId: string;
  target: string;
  model: string;
  status: Status;
  phase: "research" | "conversation" | "source";
  collectionMode: "research" | "sources";
  connection: "connected" | "reconnecting";
  error: string;
  logs: ArcEvent[];
  graph: GraphData;
  prompts: HumanPrompt[];
  judgment: { archetype: string; score: number | null; summary: string } | null;
  committed: boolean;
  cost: number;
  cacheRead: number;
  portrait: PortraitSection[];
};

export const initialState: InvestigationState = {
  sessionId: "", target: "", model: "", status: "idle", phase: "research", connection: "connected",
  collectionMode: "research",
  error: "", logs: [], graph: { nodes: [], links: [] }, prompts: [], judgment: null,
  committed: false, cost: 0, cacheRead: 0, portrait: [],
};

export function applyEvent(state: InvestigationState, event: ArcEvent): InvestigationState {
  let logs = state.logs;
  const last = logs.at(-1);
  if (event.type === "agent.text" && last?.type === "agent.text" && last.subagent === event.subagent) {
    logs = [...logs.slice(0, -1), { ...last, text: (last.text || "") + (event.text || "") }];
  } else {
    logs = [...logs, event];
  }
  const next = { ...state, logs };
  switch (event.type) {
    case "case.ready":
      return { ...next, status: "complete", collectionMode: "sources" };
    case "source.started":
      return { ...next, status: "running", phase: "source", error: "" };
    case "source.complete":
      return { ...next, status: "complete", phase: "source" };
    case "source.failed":
    case "source.cancelled":
      return { ...next, status: event.type === "source.cancelled" ? "cancelled" : "failed", phase: "source", error: event.error || "Source lookup did not finish." };
    case "conversation.started":
      return { ...next, status: "running", phase: "conversation", error: "" };
    case "conversation.complete":
      return { ...next, status: "complete" };
    case "conversation.cancelled":
      return { ...next, status: "cancelled", error: event.text || "Reply stopped." };
    case "conversation.failed":
      return { ...next, status: "failed", error: event.error || "Reply failed." };
    case "investigation.started":
      return { ...next, status: "running", target: event.target || state.target, collectionMode: event.mode || "research",
        graph: { nodes: [{ id: "root_target", title: event.target || state.target,
          label: event.mode === "sources" ? "ResearchTarget" : "Person", isDraft: true }], links: [] } };
    case "investigation.collecting":
      return { ...next, model: event.model || "", status: "running" };
    case "graph.draft_node": {
      if (state.committed) return next;
      const id = event.id || event.evidence_id;
      if (!id || state.graph.nodes.some((node) => node.id === id)) return next;
      return { ...next, graph: {
        nodes: [...state.graph.nodes, { id, title: event.preview || event.source || "Evidence",
          label: "DraftSignal", isDraft: true, evidence_ids: event.evidence_id ? [event.evidence_id] : [] }],
        links: [...state.graph.links, { source: "root_target", target: id, relationship: "RAW_SIGNAL" }],
      } };
    }
    case "human.interrupt":
      if (!event.request_id || !event.question) return next;
      return { ...next, prompts: [...state.prompts.filter((p) => p.request_id !== event.request_id), {
        request_id: event.request_id, question: event.question,
        candidates: event.candidates || [], multi_select: event.multi_select || false,
      }] };
    case "human.resolved":
      return { ...next, prompts: state.prompts.filter((p) => p.request_id !== event.request_id) };
    case "agent.architect_decision":
      return { ...next, judgment: { archetype: event.archetype || "Unknown",
        score: event.score ?? null, summary: event.summary || "" } };
    case "graph.final_commit":
      if (!event.graph) return next;
      return { ...next, committed: true, portrait: event.graph.portrait || [], graph: {
        nodes: event.graph.nodes.map(({ node_id, ...node }) => ({ ...node, id: node_id, isDraft: false })),
        links: event.graph.edges.map((edge) => ({ source: edge.source_id, target: edge.target_id,
          relationship: edge.relationship, reason: edge.reason })),
      } };
    case "agent.usage":
      return { ...next, cost: state.cost + (event.cost_usd || 0),
        cacheRead: state.cacheRead + (event.cache_read_input_tokens || 0) };
    case "investigation.complete":
      return { ...next, status: "complete", prompts: [] };
    case "investigation.failed":
      return { ...next, status: "failed", prompts: [],
        error: `${event.phase || "Investigation"} failed (${event.error || "unknown error"}).` };
    case "investigation.cancelled":
      return { ...next, status: "cancelled", prompts: [] };
    default:
      return next;
  }
}

export function safeSourceUrl(value?: string): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    if (["https:", "http:"].includes(url.protocol) && !url.username && !url.password) return url.href;
  } catch { /* Invalid source URLs are rendered as text only. */ }
}
