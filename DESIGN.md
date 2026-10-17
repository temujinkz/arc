---
name: ARC Workspace
description: Warm editorial research tools with a persona-led Council surface.
colors:
  workspace-bg: "#f7f6f0"
  sidebar-bg: "#eeeee6"
  text: "#292f2a"
  text-secondary: "#292f2a"
  text-muted: "#61695f"
  emerald: "#295e49"
  emerald-action: "#295e49"
  action-ink: "#fff"
  subtle-edge: "#d9ddd1"
  council-ink: "#292f2a"
  council-muted: "#61695f"
  council-surface: "#e9ece3"
  council-paper-ink: "#292d26"
  council-action: "#295e49"
  council-action-hover: "color-mix(in srgb, #295e49 90%, #fffefa)"
  council-action-ink: "#fff"
  sage: "#c5d2b2"
  sage-paper: "#e3e9da"
  sage-deep: "#42533b"
  ochre: "#debf7b"
  ochre-paper: "#f0e5c9"
  ochre-deep: "#71582b"
  mauve: "#cab8d4"
  mauve-paper: "#e8e0eb"
  mauve-deep: "#64506f"
  clay: "#dba993"
  clay-paper: "#efe0d6"
  clay-deep: "#79513e"
typography:
  body:
    fontFamily: "Geist, Arial, sans-serif"
    fontSize: "14px"
    lineHeight: "20px"
  label:
    fontFamily: "Geist, Arial, sans-serif"
    fontSize: "12px"
    lineHeight: "16px"
  metadata:
    fontFamily: "Geist, Arial, sans-serif"
    fontSize: "11px"
  caption:
    fontFamily: "Geist, Arial, sans-serif"
    fontSize: "10px"
  workspace-editorial:
    fontFamily: "Newsreader, Georgia, serif"
    fontSize: "36px"
    lineHeight: "40px"
  council-display:
    fontFamily: "Georgia, 'Times New Roman', serif"
    fontSize: "3.1rem"
    fontWeight: 400
    lineHeight: 1.08
    letterSpacing: "-.035em"
  council-headline:
    fontFamily: "Georgia, 'Times New Roman', serif"
    fontSize: "1.9rem"
    fontWeight: 400
    lineHeight: 1.16
    letterSpacing: "-.025em"
  council-body:
    fontFamily: "Geist, Arial, sans-serif"
    fontSize: "13px"
    lineHeight: 1.9
  council-question:
    fontFamily: "Georgia, 'Times New Roman', serif"
    fontSize: "17px"
    lineHeight: 1.6
rounded:
  control: "8px"
  council-control: "7px"
  paper: "12px"
  composer: "16px"
spacing:
  unit: "4px"
  compact: "8px"
  control: "12px"
  group: "16px"
  reading-gap: "20px"
  section: "24px"
  wide: "32px"
components:
  button-primary:
    backgroundColor: "{colors.emerald-action}"
    textColor: "{colors.action-ink}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
  button-primary-hover:
    backgroundColor: "{colors.emerald}"
  button-council:
    backgroundColor: "{colors.council-action}"
    textColor: "{colors.council-action-ink}"
    rounded: "{rounded.council-control}"
    padding: "11px 18px"
  button-council-hover:
    backgroundColor: "{colors.council-action-hover}"
  navigation-current:
    backgroundColor: "color-mix(in srgb, #295e49 10%, transparent)"
    textColor: "{colors.emerald}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  council-paper:
    textColor: "{colors.council-paper-ink}"
    rounded: "{rounded.paper}"
    padding: "26px"
---

# Design System: ARC Workspace

## Overview

**Creative North Star: "The Evidence Workspace"**

The Next.js workspace is a quiet, dense operating environment: warm paper surfaces, compact sans-serif controls, restrained green actions and persistent investigation navigation. This document covers `frontend/`'s workspace shell and its Council extension. `landing/` is explicitly outside this boundary and retains its separate identity.

The Council keeps that shell and introduces an editorial reading layer: serif headlines, connected persona selectors and lightly tinted reading papers. Its typography and four palettes are local to Council, not a replacement identity for other tools. Recorded values come from the finished source; the sidecar is a representative component sampler, not a new component API.

**Key Characteristics:**

- Warm paper shell with tonal separation and compact controls.
- Geist interface text, Newsreader workspace headings and Georgia Council readings.
- Persona color stays consistent between selection, preview and reading.
- Evidence access remains visible within each reading.

## Colors

### Primary

The shared green accent identifies workspace actions, links and activity. Council uses the same accent for its reading trigger and links.

### Secondary

Council's four color families pair an accent, light paper and deep ink: sage for Carl Jung, ochre for Diogenes, mauve for Friedrich Nietzsche and clay for the Builder. The deep ink colors selected initials; the accent colors selection marks; the paper supports previews and takes; deep ink supports persona details and actions on paper.

**The Persona Continuity Rule.** Preserve a persona's accent/paper/deep family across its selector, preview and reading.

### Neutral

Workspace and sidebar backgrounds separate the work area from navigation. Dark ink and muted green-gray text carry interface hierarchy; fine neutral edges divide warm paper regions. Council uses the same shell colors and dark ink on its tinted papers. Exact reusable colors are defined only in the frontmatter.

## Typography

**Body Font:** Geist, Arial, sans-serif. **Workspace Editorial:** Newsreader, Georgia, serif. **Council Serif:** Georgia, 'Times New Roman', serif. Geist and Newsreader are bundled locally.

The workspace uses a compact sans-serif ramp, principally body (14px/20px), labels (12px/16px) and small control/status text (10–13px). Council adds a much larger regular-weight serif display while keeping interface controls sans-serif.

- **Workspace metadata and captions:** the metadata role covers compact status annotations; the caption role covers secondary view labels and session identifiers. These existing steps are for brief secondary text, not body copy. Keep these roles for concise secondary text.
- **Council display:** the intro uses the frontmatter display role; at viewport widths up to 1100px it becomes (2.6rem), then (2.15rem) up to 640px. Its emphasized second line is italic.
- **Council headlines:** take headlines use the frontmatter headline role, reducing to (1.75rem) up to 640px. The results heading is (1.8rem/1.3).
- **Reading body:** verdicts use the Council body role and a maximum measure (70ch); supporting details use (12px/1.8). Intro text is (14px/1.8) with a maximum measure (60ch).
- **Questions and provocations:** questions are italic with the frontmatter question role; persona provocations use italic serif (15px/1.5), reducing to (14px) on mobile.

**The Local Serif Rule.** Keep Georgia reading headlines and questions local to Council. Workspace editorial headings use Newsreader; navigation and controls use Geist.

## Layout

The shell fills the dynamic viewport and clips outer overflow. A fixed-width sidebar (256px) sits beside a flexible, independently scrolling content area; the header is (64px) high. At widths below 768px navigation opens in a full-height native dialog. The toggle can collapse the persistent sidebar on larger screens. Header horizontal padding changes from (16px) to (24px) at 640px.

Council centers within the content area at a maximum width (1180px). Default padding is (36px 36px 64px), becoming (26px 24px 48px) up to 1100px and (18px 18px 40px) up to 640px. The interface uses a 4px spacing unit with recurring (8px, 12px, 16px, 20px, 24px, 32px) groups; Council's reading padding is optically adjusted rather than forced onto that scale.

Four connected persona selections occupy one row. Up to 640px they form a two-by-two grid with shared dividers; the run control stacks beneath the evidence summary and becomes full width. The preview changes from two columns (1:1.5) to one, and the rotated Council stamp disappears. Takes use two columns with a (20px) gap above 1100px, then a single column. The saved-reading heading and selector stack up to 640px. Take padding changes from (26px) to (22px), and detail labels narrow from (76px) to (65px).

## Elevation & Depth

Tonal backgrounds and fine dividers carry most of the workspace hierarchy. Council's papers are flat, unshadowed surfaces; the persona selector is a connected strip rather than four floating cards. The chat composer has a soft shadow, while modal evidence and navigation use translucent ink backdrops. Do not infer a global ban on gradients, borders or soft shadows from Council's flatter treatment.

**The Flat Paper Rule.** Keep Council previews and takes unshadowed; their tinted paper and spacing provide separation.

## Shapes

Workspace controls commonly use softly rounded corners (8px); the composer is broader (16px). Council's start control uses (7px), papers use (12px), and circular selection marks are (19px). Connected persona selections keep square shared edges. Council's paper dividers are fine translucent dark strokes; the selector's outer and shared dividers use the shared neutral line.

## Components

### Buttons

Workspace primary buttons use the green accent, white text, semibold labels and compact padding. Hover reduces fill opacity; disabled opacity is (0.4). The sidebar's new-investigation action uses the same green fill and white text.

Council's primary action is at least (44px) high, with the shared green fill, white ink and a right-arrow SVG. Hover lightens it; disabled controls use opacity (0.45). Selection and start controls disable during work. Council button color/border transitions last (160ms); workspace utility transitions use Tailwind's (150ms) default.

Global keyboard focus is a visible accent outline (2px, 3px offset). Council uses the same accent, and paper controls use their persona's deep ink. Reduced-motion preferences remove transitions and activity rotation.

### Inputs / Fields

The chat composer places a transparent, resizable textarea in a warm surface container with a fine neutral border. Focus within uses a translucent accent border; the submit control is a small green circular button. Council's saved-reading select uses the warm surface fill, fine stroke, compact sans-serif text and a minimum height (38px); it expands with its label on mobile.

### Navigation

The sidebar uses left-aligned icon-and-label rows, soft corners and (13px) labels. The current row has a translucent accent fill and accent text. Other rows gain a wash fill and ink text on hover. The same navigation is reused inside the mobile dialog. Icons come from Lucide as SVG.

### Persona Selection

The connected strip is a multi-select fieldset with a selected count. Selected cells gain the shared wash surface, persona deep-ink initials and a filled circular check. Hover uses the sidebar tone; `aria-pressed` expresses selection. A preview follows the last focused persona until readings exist.

### Reading Papers

Each paper contains a persona byline and stance, serif headline, verdict, structured upside/friction, italic question and collapsible receipts. Copy controls switch to a check and “Copied” for two seconds; a failed copy reveals a manual-selection hint. Receipts expose limitations and underlined evidence actions. The disclosure chevron reverses when open. A saved-reading selector and timestamp sit above the papers; changed evidence is indicated in the reading note. These are interpretations, visually distinct from the evidence they open.

### Feedback

Initial loading uses a quiet wash block; active deliberation uses a rotating SVG and a serif status line while previous takes remain available. A danger-colored outlined alert contains the retry action. Empty selection and missing-evidence guidance remain inline beside the workflow. The sidecar records actual focus, disabled, hover and reduced-motion treatments for its samples.

## Do's and Don'ts

### Do:

- **Do** retain the warm editorial workspace shell and compact sans-serif controls around Council.
- **Do** preserve each persona's accent/paper/deep color relationship.
- **Do** keep questions and receipts within their reading paper.
- **Do** preserve keyboard focus, meaningful disabled states and reduced-motion behavior.

### Don't:

- **Don't** apply Council's serif type and tinted papers to unrelated workspace surfaces by default.
- **Don't** treat `landing/` as governed by this workspace record.
- **Don't** turn uppercase eyebrows or Unicode icon substitutes into patterns for new UI.

Not canonized: incumbent uppercase eyebrows and Unicode external-link arrows are carried defects, not reusable system rules. Council's oversized preview initials and rotated stamp are local compositions, not global typography or layout tokens.

Source basis: `frontend/app/globals.css`, `frontend/components/workspace.tsx`, `investigation-sidebar.tsx`, `chat-composer.tsx`, `judgment-council.tsx`, `judgment-council.css`, and the installed Tailwind theme. This record does not claim a full accessibility audit of the incumbent workspace.
