# ARC

ARC is a local workspace for exploring a person's public professional evidence.
It supports explicit source collection, model-assisted investigations, sourced dossiers,
graphs and evidence-only conversation. The separate landing site introduces the project
and can start a research run; the Next.js workspace contains the research tools.

## The Council

After collecting public sources, the operator can open The Council to compare deliberately
opinionated readings of the same evidence. The four available lenses are Carl Jung,
Diogenes, Friedrich Nietzsche and a fictional Builder. Historical voices are inspired
interpretations, not authentic quotations or claims about the historical figures' views.

The operator selects one to four personas and explicitly starts a model reading.
Each take contains a headline, verdict, upside, tension, question, evidence references
and limitations. Thin or irrelevant evidence can produce a withheld judgment.
The interface supports copying questions, opening captured evidence and revisiting
saved readings. New evidence is flagged against the original reading snapshot.

## Trust boundaries

- Source observations, persona readings, graph assessments and peer claims remain distinct.
- Council input contains captured public source evidence; it excludes private notes,
  peer claims, previous model judgments and human free-text decisions.
- Personas critique visible work and public claims without diagnosing people or asserting
  private motives. Matching handles remain unverified identity leads.
- Reading history retains its evidence snapshot and timestamps. Opening or replaying it
  does not start another model call.
- Model calls use the existing configured model and remaining application spending
  threshold. Unknown or interrupted outcomes are preserved and never retried automatically.

## Interface context

The Council extends the warm editorial workspace. It is an operating tool: persona
selection, a clear start control and inspectable evidence take priority. Editorial
headlines, distinct persona colors and pointed questions provide its playful character.
The surrounding workspace and the separate landing site's identity remain their own surfaces.

This document records behavior shipped in the repository. The README and AGENTS.md
describe setup, storage, provider authorization and required offline checks.
