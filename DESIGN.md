---
name: Transcript Analyzer
description: Personal intelligence terminal for an executive's meeting pipeline — raw transcripts in, distilled signal and obligations out.
colors:
  bg: "#0f1117"
  surface: "#1a1d27"
  border: "#2a2d3a"
  text: "#e2e4ec"
  muted: "#7a7f96"
  accent: "#4f8ef7"
  green: "#3ecf8e"
  orange: "#f7a24f"
  red: "#f76f6f"
  purple: "#b08ef7"
  cyan: "#3ecfcf"
  gold: "#cfaf3e"
typography:
  ui:
    fontFamily: '"SF Pro Text", system-ui, sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: '"SF Pro Text", system-ui, sans-serif'
    fontSize: "11px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.05em"
  mono:
    fontFamily: '"SF Mono", "Menlo", monospace'
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
  pill: "10px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-primary-hover:
    backgroundColor: "{colors.accent}"
    textColor: "#ffffff"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-danger:
    backgroundColor: "#3a1f1f"
    textColor: "{colors.red}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  badge-exec:
    backgroundColor: "#3a2020"
    textColor: "{colors.red}"
    rounded: "{rounded.sm}"
    padding: "2px 7px"
  badge-solution:
    backgroundColor: "#1e2c3a"
    textColor: "{colors.accent}"
    rounded: "{rounded.sm}"
    padding: "2px 7px"
  badge-daily:
    backgroundColor: "#1e3a2c"
    textColor: "{colors.green}"
    rounded: "{rounded.sm}"
    padding: "2px 7px"
  badge-overdue:
    backgroundColor: "#4a1010"
    textColor: "{colors.red}"
    rounded: "{rounded.sm}"
    padding: "2px 7px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "20px"
  stat:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "14px 20px"
---

# Design System: Transcript Analyzer

## Overview

**Creative North Star: "The Intelligence Terminal"**

This interface is a classified-grade analyst's workspace. Raw transcripts and meeting notes flow in through a background pipeline; distilled obligations, decisions, and program signal emerge on screen. The design premise is that the *analysis is the product* — the interface exists only to surface it with zero friction and maximum legibility. In a tool this personal and this operational, every pixel that isn't carrying signal is overhead.

The palette is near-black with surgical precision accents. Category signals (EXEC, SOLUTION, DAILY, overdue acts) are color-coded in the same semantic vocabulary across every surface — once learned, a glance at the file table communicates the morning's meeting mix before a single word is read. Depth comes from tonal layering, not shadows: the hierarchy is `--bg` → `--surface` → foreground, and you always know where you are in it.

Interactive components are precise and considered. Buttons have a hair of transition on hover; badges use deeply-saturated-but-dark backgrounds that read clearly without shouting. Nothing moves unless it's earning its motion — the spinner on a running job, the scroll auto-advance in the output panel.

**Key Characteristics:**
- Near-black field (`#0f1117`) as the absolute base — the screen recedes
- Semantic color vocabulary: seven named signal colors, each owned by a single role
- Flat tonal layering, no drop shadows anywhere
- 11px uppercase tracked labels for all section headers and table columns
- SF Pro Text / system-ui as the sole prose face; SF Mono exclusively for code and live job output
- Dense information layout — 14px base size, 1100px content cap, no wasted whitespace

## Colors

A near-monochromatic dark field punctuated by six precisely-owned signal colors. Hue is meaning: no color appears in two roles.

### Primary
- **Signal Blue / Accent** (`#4f8ef7`): Interactive affordances — primary buttons, active nav state, form focus rings, quick-link color. The single "do this" color.

### Secondary
- **Confirmation Green** (`#3ecf8e`): Success states, "shareable" confirmation ticks, notes badge, positive flash messages. Implies "this worked."
- **Warning Orange** (`#f7a24f`): Standup badge, deferred item badge. Implies "watch this."

### Tertiary
- **Alert Red** (`#f76f6f`): EXEC badge, overdue badge, error flash, triage alert banner, danger button text. Implies "act now."
- **Delegation Purple** (`#b08ef7`): They-owe badge. Named to distinguish "other person's ownership."
- **Watch Cyan** (`#3ecfcf`): Insight badge. Quiet, informational — not urgent.
- **Deferred Gold** (`#cfaf3e`): Deferred badge text and the inbox card header — the only warm-yellow signal, owned by the "not now, but not dropped" state.

### Neutral
- **Deep Void** (`#0f1117`): Page background. The terminal floor.
- **Surface Layer** (`#1a1d27`): Cards, header, stat tiles, job panel. One step above void.
- **Quiet Border** (`#2a2d3a`): All dividers, card outlines, input borders, table row separators.
- **Primary Text** (`#e2e4ec`): All body copy, table cells, nav labels, badge text.
- **Muted Text** (`#7a7f96`): Timestamps, cost figures, section headers in cards, placeholder and secondary metadata.

### Named Rules
**The One Hue, One Role Rule.** Each signal color — blue, green, orange, red, purple, cyan, gold — is owned by exactly one semantic role. Never repurpose `--accent` for decoration, never use `--green` for anything other than success/confirmation, and never use `--red` for anything other than urgency/danger. The vocabulary works because it's consistent; a single exception breaks the whole system.

**The Neutral Majority Rule.** At least 90% of any given screen surface uses only the four neutral tokens. Signal colors are the 10% that earns attention by being rare.

## Typography

**UI Font:** SF Pro Text (with system-ui, sans-serif fallback)
**Mono Font:** SF Mono (with Menlo, monospace fallback)
**No display font** — this is an operational tool, not a publication. The typographic system is entirely functional.

**Character:** Clean, dense, zero-expressive. SF Pro Text at 14px/1.5 is a deliberate choice for a macOS-native tool — it matches the OS's own UI chrome and disappears into the content. SF Mono appears exclusively for machine-generated text (job output, file paths, env keys) where fixed pitch is semantically meaningful.

### Hierarchy
- **Section header** (700 weight, 13px, uppercase, 0.06em tracking): Card `h2` elements and section dividers. The workhorse label — everything that organizes content uses this.
- **Stat value** (700 weight, 22px): The bold number in stat tiles. The only large text in the system; reserved for counted quantities.
- **Body / Table** (400 weight, 13–14px, 1.5 line-height): All table cells, item text, form descriptions, flash messages.
- **Label / Badge** (700 weight, 11px, uppercase, 0.04–0.07em tracking): Category badges, column headers, quick-link labels, timestamp-class metadata.
- **Mono / Output** (400 weight, 12px/1.6, SF Mono): Job output panel, file paths, `<code>` inline fragments.

### Named Rules
**The Uppercase Label Rule.** Organizational text — card section headers, table column headers, badge text, stat labels — is always 11px uppercase with letter-spacing. Body text is never uppercase. The two registers are exclusive: you always know at a glance whether something is structure or content.

## Layout

Single-column, max-width 1100px, left-aligned, with 24px horizontal page padding. No responsive breakpoints — the tool is desktop-only. The header is a fixed horizontal strip (surface background, 1px bottom border) holding the wordmark, nav, and the shared-folder CTA at the far right.

**Spatial rhythm:** spacing uses a 4px base step: xs=4px, sm=8px, md=16px, lg=24px. Cards are spaced 20px from each other vertically. Table rows use 7px vertical padding — dense enough to scan a 50-row log without scrolling far, open enough to read clearly.

**Stat tiles:** a flex-wrap row of min-width 140px tiles above the action row. Each tile shows one counted quantity with a 22px bold number and an 11px muted label. The row expands naturally with the pipeline's available metrics.

**Action row:** a horizontal strip of button+date-input pairs, one per synthesis mode. Tightly packed, no vertical stacking — these are keyboard-proximate triggers, not navigational items.

**Content cap:** 1100px prevents line lengths from becoming unwieldy on large monitors. Below 1100px, it fills the viewport.

## Elevation & Depth

This system is completely flat. No box-shadows exist anywhere in the codebase. Depth is communicated exclusively through tonal layering:

- `--bg` (`#0f1117`) → lowest layer, the page itself
- `--surface` (`#1a1d27`) → raised surfaces: cards, header, stat tiles, input fields, the job panel
- `--border` (`#2a2d3a`) → outlines that separate `surface` from `surface` (table dividers, card borders, input borders)

This creates a predictable 2.5-level depth model: floor, surface, edge. Nothing floats. Nothing casts a shadow.

### Named Rules
**The No-Shadow Rule.** Drop shadows are permanently absent. When a surface needs to read as elevated, it uses `--surface` background against a `--bg` surround, optionally framed by a `1px --border` outline. Adding shadows is a hard regression.

## Shapes

Consistently gently-curved with a four-step radius scale: `4px` (sm — badges, inner elements), `6px` (md — buttons, interactive controls), `8px` (lg — cards, stat tiles, panels), `10px` (pill — nav badge count). The form language is rectilinear with rounded corners that soften without becoming friendly. No circles, no fully-rounded cards, no sharp 0px corners anywhere in the interactive layer.

**Toggle switch:** uses its own geometry — 36×20px pill track, 14×14px circular thumb — consistent with iOS/macOS system toggle conventions.

**The Consistent Rounding Rule.** Radius tier is determined by component class: cards are always 8px, buttons/inputs are always 6px, badges are always 4px. Never mix tiers within the same element or use ad-hoc values.

## Components

### Buttons

Precise and considered — a subtle `opacity: 0.85` on hover rather than a background shift, keeping the button's color stable while signaling responsiveness.

- **Shape:** 6px radius, 8px/16px padding, 13px bold text
- **Primary (blue):** `--accent` background, white text. Used for the single highest-priority action in a context group.
- **Secondary:** `--surface` background, `--text` color, `1px --border` outline. Used for all standard actions (Run Analysis, Weekly Delta, Catch Up, Career, Timecard).
- **Danger:** `#3a1f1f` background, `--red` text, `1px #5a2e2e` border. Used for destructive confirmations (Drop, Re-own) inside the brief-state panel.
- **Hover:** `opacity: 0.85` on all variants. No background swap, no movement.

### Badges

Badges are the semantic color vocabulary made visible. They are never decorative.

- **Shape:** 4px radius, 2px/7px padding, 11px bold uppercase, 0.04em tracking
- **EXEC:** `#3a2020` background, `--red` text
- **SOLUTION:** `#1e2c3a` background, `--accent` text
- **DAILY:** `#1e3a2c` background, `--green` text
- **STANDUP:** `#2a2c1e` background, `--orange` text
- **NOTES:** a purple-tinted dark, `#b08ef7` text
- **OVERDUE:** `#4a1010` background, `--red` bold text — the loudest badge in the system
- **ACT / WATCH / DELEGATE / THEY-OWE / INSIGHT** follow the same dark-saturated-tint pattern keyed to their semantic color

### Cards / Containers

The primary structural unit. All content lives in cards.

- **Corner Style:** 8px radius
- **Background:** `--surface` (`#1a1d27`)
- **Border:** `1px solid --border`
- **Shadow:** none
- **Padding:** 20px all sides
- **Section title (`h2`):** 13px bold uppercase, `--muted` color, 14px bottom margin

### Inputs / Fields

- **Style:** `--bg` background (recessed below surface), `1px --border`, 6px radius, 13px text, 7px/10px padding
- **Focus:** border shifts to `--accent` (no glow, no box-shadow — consistent with the no-shadow rule)
- **Date pickers:** same base style; appear inline in the action row alongside buttons
- **Textarea (Slack Paste):** same tokens, 360px min-height, vertical resize only
- **Toggle switch:** 36×20px pill track (`--border` unchecked, `--accent` checked), 14×14px circular thumb. Hidden `<input>` has `position: absolute`; keyboard focus ring (2px `--accent` outline) appears on the `.slider` track via `:focus-visible + .slider` — visible to keyboard users without disrupting mouse-driven layout.

### Navigation

- **Strip:** horizontal flex in the header, `--surface` background
- **Links:** 13px, default `--muted` color, 6px/10px padding, 6px radius
- **Hover / Active:** `--text` color, `--border` background fill
- **Pending-count badge:** red pill (`--red` background, white text, 10px font, 10px radius) appended inline to the Brief State nav link

### Stat Tiles

A signature component of the dashboard. Flex-wrap row of minimum-width tiles, each showing one pipeline metric.

- **Background:** `--surface`, 8px radius, `1px --border`, 14px/20px padding
- **Value:** 22px bold `--text` — the largest text in the entire system
- **Label:** 11px uppercase `--muted`, 0.05em tracking, 2px top margin

### Job Output Panel

The live terminal embed shown when a synthesis or analysis job is running.

- **Container:** `--surface` background, `1px --border`, 8px radius, 16px padding
- **Output text:** 12px SF Mono, `--text`, 1.6 line-height, `pre-wrap`, `overflow-wrap: break-word`, max-height 320px, scroll-to-bottom on update
- **Status line:** 12px uppercase `--muted`, with an inline CSS animation spinner (`--border`/`--accent`) when running
- **Failure state:** the "done" indicator turns `--red` and the panel stays visible — never auto-reloads a failed job away
- **Accessibility:** `role="log"` and `aria-live="polite"` on the output container so screen readers announce streamed lines

### Alert Banner (Triage)

The highest-urgency surface. Appears only when undismissed triage alerts exist.

- **Container:** `#2a1a1a` background, `1px #6b2f2f` border, 8px radius — a warm dark red that reads "urgent" before the header text registers
- **Header:** `--red` uppercase bold text + dismiss-all ghost button at the far right
- **Items:** separated by `#3a2020` top borders; dismiss button per item

## Do's and Don'ts

### Do:
- **Do** use `--accent` (`#4f8ef7`) for all and only interactive affordances — links, focus rings, primary buttons, active nav state.
- **Do** keep table rows at 7px vertical padding. Denser obscures; looser wastes the density advantage.
- **Do** use SF Mono only for machine-generated or machine-readable text: job output, file paths, inline code, `.env` key names.
- **Do** keep the stat tile value at 22px bold — it's the only large text in the system. A second large-text element breaks the visual hierarchy.
- **Do** use the dark-tinted badge pattern (dark saturated background + colored text) for all new semantic badge types — never a solid bright background.
- **Do** make the OVERDUE badge (`#4a1010` / `--red` bold) the most visually prominent badge. It should be louder than everything except the triage alert banner.
- **Do** apply `overflow-wrap: break-word` to all flex-child text containers (`.bs-text`, `.alert-text`, job output). AI-generated obligation text and long meeting titles will shatter flex layouts without it.
- **Do** truncate the source-file column in tables (`max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap`) and expose the full name in a `title` attribute.

### Don't:
- **Don't** add box-shadows. The depth model is tonal. Any shadow is a regression.
- **Don't** introduce new accent colors. The six signal colors are complete: blue (action), green (success), orange (warning), red (danger/urgency), purple (delegation), cyan (information). A seventh color dilutes the vocabulary.
- **Don't** use color for decoration. Every non-neutral color on screen must carry semantic meaning.
- **Don't** mix radius tiers within a component. Cards are 8px. Buttons/inputs are 6px. Badges are 4px. No ad-hoc values.
- **Don't** use uppercase for body text. The uppercase register is exclusively structural (labels, badges, column headers). Body text and item text are sentence case.
- **Don't** auto-reload the page after a failed job. The output panel must stay visible when `returncode !== 0`. Reloading a failure away is a loss of diagnostic signal.
- **Don't** introduce new accent colors. The seven signal colors are complete: blue (action), green (success), orange (warning), red (danger/urgency), purple (delegation), cyan (information), gold (deferred). An eighth color dilutes the vocabulary.
