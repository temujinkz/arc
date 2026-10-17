import type { ArcEvent, InvestigationState } from "./investigation";

export function toolLabel(tool = "") {
  const names: Record<string, string> = {
    search_identity_exa: "Exa search (saved evidence)", search_web_you: "You.com web search", scrape_linkedin_profile: "LinkedIn profile",
    scrape_linkedin_posts: "LinkedIn posts", inspect_github_developer_profile: "GitHub profile",
    firecrawl_scrape: "Read website", get_file_contents: "Read repository file",
    AskUserQuestion: "Identity clarification", Agent: "Delegate source collection",
    search_public_web: "Free web search", search_username_sherlock: "Sherlock username leads",
    search_username_maigret: "Maigret username leads", inspect_domain_theharvester: "Domain certificate records",
    search_publications_crossref: "Publication search", robin_search: "Robin search", robin_scrape: "Robin source read",
    inspect_domain_dns: "Public DNS", inspect_domain_archive: "Archived website pages",
  };
  const name = tool.split("__").at(-1) || tool;
  return names[name] || name.replaceAll("_", " ");
}

export function provider(tool = "") {
  if (tool.includes("linkedin")) return "Apify";
  if (tool.includes("exa")) return "Exa";
  if (tool.includes("search_web_you")) return "You.com";
  if (tool.includes("github")) return "GitHub";
  if (tool.includes("firecrawl")) return "Firecrawl";
  if (tool.includes("neo4j")) return "Neo4j";
  if (tool.includes("search_public_web")) return "Free search";
  if (tool.includes("sherlock")) return "Sherlock";
  if (tool.includes("maigret")) return "Maigret";
  if (tool.includes("theharvester")) return "theHarvester";
  if (tool.includes("crossref")) return "Crossref";
  if (tool.includes("robin")) return "Robin";
  if (tool.includes("inspect_domain_dns")) return "Public DNS";
  if (tool.includes("inspect_domain_archive")) return "Internet Archive";
  return "Claude";
}

export function toolResults(logs: ArcEvent[]) {
  return new Map(logs.filter((event) => event.tool_use_id &&
    ["tool.finished", "tool.failed"].includes(event.type)).map((event) => [event.tool_use_id!, event]));
}

export function activityLabel(state: InvestigationState) {
  if (state.connection === "reconnecting") return "Reconnecting to the event stream";
  if (state.prompts.length) return "Waiting for your answer";
  if (state.status === "starting") return "Starting investigation";
  if (state.status !== "running") return "";
  if (state.phase === "conversation") return "Answering from collected evidence";
  const results = toolResults(state.logs);
  const active = state.logs.filter((event) => event.type === "tool.started" && !results.has(event.tool_use_id || ""));
  if (active.length) return toolLabel(active.at(-1)?.tool);
  if (state.logs.some((event) => event.stage === "architecting")) return "Preparing the graph assessment";
  return "Agent reviewing sources and planning the next step";
}
