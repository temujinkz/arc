export type Kind = "person" | "company" | "theme" | "artifact" | "claim" | "judgment";

export type GNode = { id: string; label: string; kind: Kind; note?: string; sub?: string; hue?: string; draft?: boolean };
export type GLink = { source: string; target: string; rel: string };

export type Person = { id: string; name: string; role: string; company: string; area: string };

// Names, titles and companies as shown on the first round judges slide. Nothing else is
// assumed about these people. Replace with the arc-engine API response.
const judges: [string, string, string, string][] = [
  ["Raj Jagiasi", "Founder", "Elite", "Founders"],
  ["Janaki Ram Goteti", "Sr. Staff Software Engineer", "Crusoe", "Engineering"],
  ["Martin Cala", "Staff Solutions Engineer", "Crusoe", "Engineering"],
  ["Sanjana Somayajula", "Product Manager", "NVIDIA", "Product"],
  ["Michelle Julia N.", "Founder", "Blue", "Founders"],
  ["Pranay Periwal", "Founding Project Manager", "Color", "Product"],
  ["George Gulabyan", "Sr. Product Manager", "Crusoe", "Product"],
  ["Hemant Sethi", "Staff Software Engineer", "Crusoe", "Engineering"],
  ["Abhinav Balasubramanian", "Staff AI Engineer", "NVIDIA", "Engineering"],
  ["George Kalangi", "Technical Product Manager", "Apple", "Product"],
  ["Kaan Yigit", "Co-founder & CTO", "Stoa", "Founders"],
  ["Shubham Amilkanthwar", "Sr. Data Engineer", "Crusoe", "Engineering"],
  ["Rohit Kalmankar", "Sr. Staff Solutions Engineer", "Crusoe", "Engineering"],
  ["Didi Kamalova", "System Software Engineer", "NVIDIA", "Engineering"],
  ["Christina Lucey", "Product Lead", "You.com", "Product"],
];

// Colour carries meaning: a person takes the colour of their role area, companies stay ink.
export const areaHue: Record<string, string> = { Founders: "#ee8d3a", Engineering: "#4a7fd0", Product: "#98368f" };
export const kindHue: Partial<Record<Kind, string>> = { company: "#15140f", theme: "#4a7fd0", artifact: "#5b9a3a", claim: "#ee8d3a", judgment: "#cf3522" };

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

export const people: Person[] = judges.map(([name, role, company, area]) => ({ id: slug(name), name, role, company, area }));

export const network: { nodes: GNode[]; links: GLink[] } = (() => {
  const nodes: GNode[] = [], links: GLink[] = [];
  const seen = new Set<string>();
  const add = (n: GNode) => { if (!seen.has(n.id)) { seen.add(n.id); nodes.push(n); } };
  for (const p of people) {
    add({ id: p.id, label: p.name, kind: "person", sub: `${p.role}, ${p.company}`, hue: areaHue[p.area] });
    add({ id: "co-" + slug(p.company), label: p.company, kind: "company", sub: "Company" });
    add({ id: "area-" + slug(p.area), label: p.area, kind: "theme", sub: "Role area", hue: areaHue[p.area] });
    links.push({ source: p.id, target: "co-" + slug(p.company), rel: "works at" });
    links.push({ source: p.id, target: "area-" + slug(p.area), rel: "role" });
  }
  return { nodes, links };
})();

// An example profile. It only holds what the team has told us so far.
export const dossier: { nodes: GNode[]; links: GLink[] } = {
  nodes: [
    { id: "p", label: "Connor Guerrero", kind: "person", note: "The person this profile is about." },
    { id: "co", label: "Crusoe", kind: "company", note: "Where he works." },
    { id: "t1", label: "Machine learning", kind: "theme", note: "Taken from his job title." },
    { id: "c1", label: "ML engineer at Crusoe", kind: "claim", note: "Only one source says so, and it is our own. Treat it as unconfirmed." },
    { id: "a1", label: "Note from the Arc team", kind: "artifact", note: "Not a public source yet. A real search would add his public pages here." },
  ],
  links: [
    { source: "p", target: "co", rel: "works at" },
    { source: "p", target: "t1", rel: "works on" },
    { source: "p", target: "c1", rel: "described as" },
    { source: "c1", target: "co", rel: "about" },
    { source: "a1", target: "c1", rel: "supports" },
  ],
};

for (const n of dossier.nodes) n.hue = kindHue[n.kind];
