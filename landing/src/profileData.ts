// Turns the backend's committed graph (graph.final_commit) into plain fields a profile card can show.
// Pure functions only, so they can be checked without a browser.

export type Tone = "good" | "mid" | "warn" | "bad" | "muted";

export type ClaimView = { text: string; label: string; tone: Tone };
export type SourceView = { label: string; url: string; kind: string };
export type OrgView = { label: string; rel: string };
export type ProfileData = {
  verdict: { label: string; tone: Tone };
  claims: ClaimView[];
  sources: SourceView[];
  orgs: OrgView[];
  works: { label: string; url: string }[];
  themes: string[];
};

// How strongly the backend says a claim is backed. It decides this by rule from where the evidence came from.
const SUPPORT: Record<string, { label: string; tone: Tone }> = {
  THIRD_PARTY_SUPPORTED: { label: "Backed by an independent source", tone: "good" },
  ARTIFACT_SUPPORTED: { label: "Backed by their own work", tone: "mid" },
  SELF_REPORTED_ONLY: { label: "Only their own words", tone: "warn" },
  CONFLICTING: { label: "Sources disagree", tone: "bad" },
  CONTRADICTED: { label: "Contradicted by a source", tone: "bad" },
  UNCHECKED: { label: "Not checked yet", tone: "muted" },
};
const BY_STATE: Record<string, { label: string; tone: Tone }> = {
  supported: { label: "Backed by a source", tone: "mid" },
  disputed: { label: "Disputed", tone: "bad" },
  unverified: { label: "Not verified", tone: "warn" },
  observed: { label: "Seen in a source", tone: "mid" },
  interpretation: { label: "Our reading", tone: "muted" },
};
const SOURCE_KIND: Record<string, string> = {
  SELF_AUTHORED: "their own page",
  ARTIFACT: "their work",
  THIRD_PARTY: "independent",
};
const REL: Record<string, string> = {
  WORKED_AT: "worked at",
  CONTRIBUTED_TO: "contributed to",
  COLLABORATED_ON: "collaborated on",
};

export type RawNode = { node_id: string; label: string; title?: string; summary?: string; url?: string; evidence_state?: string; metadata?: Record<string, unknown> };
export type RawEdge = { source_id: string; target_id: string; relationship: string };
export type RawGraph = { summary?: string; nodes?: RawNode[]; edges?: RawEdge[]; portrait?: { title?: string; analysis?: string; limitations?: string; support_status?: string }[] };

export function host(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

// "facebook.com/temujin.media/posts/..." : the site plus as much of the path as fits, so two pages on one site read differently.
export function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    const s = u.hostname.replace(/^www\./, "") + u.pathname.replace(/\/$/, "");
    return s.length > 38 ? s.slice(0, 37) + "…" : s;
  } catch { return url; }
}

export function profileFromGraph(g: RawGraph | null | undefined): ProfileData {
  const nodes = g?.nodes ?? [], edges = g?.edges ?? [];
  const byId = new Map(nodes.map((n) => [n.node_id, n]));

  const claims: ClaimView[] = nodes.filter((n) => n.label === "Claim").map((n) => {
    const level = SUPPORT[String(n.metadata?.support ?? "")] ?? BY_STATE[n.evidence_state ?? "unverified"] ?? BY_STATE.unverified;
    return { text: n.title ?? "", label: level.label, tone: level.tone };
  });

  const seen = new Set<string>();
  const sources: SourceView[] = [];
  for (const n of nodes) {
    if (!n.url || !["EvidenceNode", "IdentityAnchor", "Artifact"].includes(n.label)) continue;
    const key = n.url.replace(/\/$/, "").toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    sources.push({ label: shortUrl(n.url), url: n.url, kind: SOURCE_KIND[String(n.metadata?.source_kind ?? "")] ?? "source" });
  }

  const orgs: OrgView[] = nodes.filter((n) => n.label === "Organization").map((n) => {
    const e = edges.find((x) => (x.source_id === n.node_id || x.target_id === n.node_id) && REL[x.relationship]);
    return { label: n.title ?? "", rel: e ? REL[e.relationship] : "linked to" };
  });
  const works = nodes.filter((n) => n.label === "Artifact" && n.title).map((n) => ({ label: n.title!, url: n.url ?? "" }));
  const themes = nodes.filter((n) => n.label === "DomainPillar" && n.title).map((n) => n.title!).slice(0, 5);

  const independent = claims.filter((c) => c.tone === "good").length;
  const verdict: ProfileData["verdict"] = !claims.length
    ? { label: "Not enough public information yet", tone: "muted" }
    : independent
      ? { label: `${independent} of ${claims.length} ${claims.length === 1 ? "claim" : "claims"} independently backed`, tone: "good" }
      : claims.some((c) => c.tone === "mid")
        ? { label: "Backed by their own work, not yet by others", tone: "mid" }
        : { label: "Not independently verified", tone: "warn" };

  return { verdict, claims, sources, orgs, works, themes };
}
