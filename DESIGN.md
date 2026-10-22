---
name: Transcript Analyzer
description: Salesforce-aligned executive command center — obligations surface, pipeline runs, signal stays scannable.
colors:
  brand: "#0176d3"
  brandDark: "#014486"
  surface1: "var(--slds-g-color-surface-1, #ffffff)"
  surface2: "var(--slds-g-color-surface-2, #f3f3f3)"
  border1: "var(--slds-g-color-border-1, #e5e5e5)"
  border2: "var(--slds-g-color-border-2, #c9c7c5)"
  text: "var(--slds-g-color-on-surface-2, #181818)"
  muted: "var(--slds-g-color-on-surface-4, #706e6b)"
  success: "var(--slds-g-color-success-2, #2e844a)"
  warning: "var(--slds-g-color-warning-2, #dd7a01)"
  error: "var(--slds-g-color-error-2, #c23934)"
  purple: "#6b3fa0"
  teal: "#0d7377"
  deferredGold: "#8a6000"
typography:
  ui:
    fontFamily: '"SF Pro Text", system-ui, sans-serif'
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: '"SF Pro Text", system-ui, sans-serif'
    fontSize: "11px"
    fontWeight: 700
    letterSpacing: "0.05em"
    textTransform: "uppercase"
  mono:
    fontFamily: '"SF Mono", "Menlo", monospace'
    fontSize: "12px"
    lineHeight: 1.6
  stat:
    fontFamily: '"SF Pro Text", system-ui, sans-serif'
    fontSize: "22px"
    fontWeight: 700
rounded:
  sm: "var(--slds-g-radius-border-1, 0.125rem)"
  md: "var(--slds-g-radius-border-2, 0.25rem)"
  lg: "var(--slds-g-radius-border-3, 0.5rem)"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "48px"
components:
  button-primary:
    backgroundColor: "var(--slds-g-color-brand-2, #0176d3)"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-primary-small:
    backgroundColor: "var(--slds-g-color-brand-2, #0176d3)"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    padding: "3px 8px"
  button-secondary:
    backgroundColor: "var(--slds-g-color-surface-1, #ffffff)"
    textColor: "var(--slds-g-color-on-surface-2, #181818)"
    border: "1px solid var(--slds-g-color-border-2, #c9c7c5)"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-secondary-small:
    backgroundColor: "var(--slds-g-color-surface-1, #ffffff)"
    textColor: "var(--slds-g-color-on-surface-2, #181818)"
    border: "1px solid var(--slds-g-color-border-2, #c9c7c5)"
    rounded: "{rounded.sm}"
    padding: "3px 8px"
  button-destructive:
    backgroundColor: "var(--slds-g-color-error-2, #c23934)"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-daily-pulse:
    backgroundColor: "var(--slds-g-color-brand-2, #0176d3)"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-periodic:
    backgroundColor: "var(--slds-g-color-surface-1, #ffffff)"
    textColor: "var(--slds-g-color-on-surface-2, #181818)"
    border: "1px solid var(--slds-g-color-border-2, #c9c7c5)"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-rare:
    backgroundColor: "var(--slds-g-color-surface-1, #ffffff)"
    textColor: "var(--slds-g-color-on-surface-4, #706e6b)"
    border: "1px solid var(--slds-g-color-border-2, #c9c7c5)"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  badge-exec:
    backgroundColor: "#fef1f1"
    textColor: "var(--slds-g-color-error-2, #c23934)"
    rounded: "{rounded.md}"
    padding: "2px 7px"
  badge-solution:
    backgroundColor: "#eaf1fb"
    textColor: "var(--slds-g-color-brand-2, #0176d3)"
    rounded: "{rounded.md}"
    padding: "2px 7px"
  badge-daily:
    backgroundColor: "#eaf5ec"
    textColor: "var(--slds-g-color-success-2, #2e844a)"
    rounded: "{rounded.md}"
    padding: "2px 7px"
  badge-standup:
    backgroundColor: "#fff3e0"
    textColor: "var(--slds-g-color-warning-2, #dd7a01)"
    rounded: "{rounded.md}"
    padding: "2px 7px"
  badge-notes:
    backgroundColor: "#f5eafb"
    textColor: "#6b3fa0"
    rounded: "{rounded.md}"
    padding: "2px 7px"
  badge-overdue:
    backgroundColor: "#fef1f1"
    textColor: "var(--slds-g-color-error-2, #c23934)"
    border: "1px solid #fac9c9"
    rounded: "{rounded.md}"
    padding: "2px 7px"
  badge-they-owe:
    backgroundColor: "#f5eafb"
    textColor: "#6b3fa0"
    rounded: "{rounded.md}"
    padding: "2px 7px"
  badge-deferred:
    backgroundColor: "#fff8e1"
    textColor: "#8a6000"
    rounded: "{rounded.md}"
    padding: "2px 7px"
  badge-delta:
    backgroundColor: "#eaf5f5"
    textColor: "#0d7377"
    rounded: "{rounded.md}"
    padding: "2px 7px"
  card:
    backgroundColor: "var(--slds-g-color-surface-1, #ffffff)"
    border: "1px solid var(--slds-g-color-border-1, #e5e5e5)"
    rounded: "{rounded.lg}"
    padding: "20px"
  card-focus:
    backgroundColor: "var(--slds-g-color-surface-1, #ffffff)"
    border: "2px solid var(--slds-g-color-brand-2, #0176d3)"
    rounded: "{rounded.lg}"
    padding: "20px"
  card-inbox:
    backgroundColor: "#fffdf0"
    border: "1px solid var(--slds-g-color-warning-2, #dd7a01)"
    rounded: "{rounded.lg}"
    padding: "20px"
---

# Design System: Transcript Analyzer

## Overview

**Creative North Star: "Salesforce Record Page for Obligations"**

The interface follows Salesforce's native record-page pattern: a clean white field, a sticky header with a brand-blue underline, horizontal subtabs for major information categories, and related-list tables for scannable row-by-row content. The tool is used by someone who lives in Salesforce — the design language should feel immediately familiar, not like a custom tool that needs to be learned.

The primary Brief State view uses five tabs (Act / Watch / Waiting / Inbox / Closed), so arriving on the page shows only the active obligations at the weight the user chooses. Operational paralysis is eliminated by making sections mutually exclusive rather than stacked. A pinned "Today's Focus" card (2px blue border) floats above the act table for the most critical items; the overdue banner is always visible above the tabs regardless of which tab is active.

Dark mode is preserved as a toggle (dimly-lit office use), but **light is the default**. In light mode, the SLDS token system provides the chrome; dark mode applies an explicit `slds-color-scheme_dark` class and overrides the hardcoded hex values. The theme preference is persisted to `localStorage`.

**Key Characteristics:**
- SLDS2 Cosmos token system (`--slds-g-color-*`, `--slds-g-font-scale-*`, `--slds-g-radius-border-*`) loaded from CDN
- White/light-grey field (Salesforce native); dark toggle preserved
- Salesforce brand blue (`#0176d3`) as the single action color
- Brief State as tab-based record page — Act / Watch / Waiting / Inbox / Closed
- Related-list tables inside each tab: grey table header row, subtle row hover, right-aligned actions
- Semantic badge vocabulary: light tinted backgrounds (not dark), each hue owns one role
- Synthesis buttons tiered by frequency: daily-pulse (blue primary), periodic (secondary), rare (muted secondary)

## Colors

All colors defer to SLDS global tokens in light mode. Dark mode applies explicit hex overrides. Static semantic colors (badge backgrounds, banner fills) use direct hex values that remain fixed across both themes.

### Brand
- **Salesforce Blue** (`#0176d3`): All interactive affordances — primary buttons, active tab underline, focus rings, links, count pills on active tabs, Today's Focus card border.

### Semantic Signals
- **Error / Urgency** (`var(--slds-g-color-error-2, #c23934)`): EXEC badge, OVERDUE badge, overdue banner, error flash, destructive buttons. The loudest signal.
- **Success** (`var(--slds-g-color-success-2, #2e844a)`): DAILY badge, NOTES badge, PENDING badge, positive flash, all-clear empty states.
- **Warning** (`var(--slds-g-color-warning-2, #dd7a01)`): STANDUP badge, deferred badge, inbox card border/header, pipeline stale indicator, Weekly Delta nudge.
- **Purple** (`#6b3fa0`): THEY-OWE badge, NOTES synthesis badge — delegation and interpersonal semantics.
- **Teal** (`#0d7377`): INSIGHT badge, DELTA badge — observation and situational awareness.
- **Deferred Gold** (`#8a6000` text on `#fff8e1` bg): Deferred state only.
- **Info Blue** (`var(--slds-g-color-brand-2, #0176d3)` on `#eaf1fb` bg): Job-running banner — in-progress, not error.

### Neutral
- `var(--slds-g-color-surface-1, #ffffff)`: Page background, card backgrounds, button-secondary background.
- `var(--slds-g-color-surface-2, #f3f3f3)`: Table header row background, program order drag items, secondary surfaces.
- `var(--slds-g-color-border-1, #e5e5e5)`: Light row dividers, card borders, separator lines.
- `var(--slds-g-color-border-2, #c9c7c5)`: Card borders on secondary cards, input borders, button-secondary borders.
- `var(--slds-g-color-on-surface-2, #181818)`: Primary text, table cell text.
- `var(--slds-g-color-on-surface-4, #706e6b)`: Muted text, timestamps, table column headers, nav links at rest, section headers.

### Named Rules
**One hue, one role.** Blue = action only. Red = urgency/error only. Yellow/warning = inbox and deferral. Never use red for a completion action (Dismiss, Done) — those are secondary buttons. The vocabulary works because it is consistent.

**The Static Hex Exception.** Badge backgrounds (`#fef1f1`, `#eaf1fb`, `#eaf5ec`, etc.) and the inbox card (`#fffdf0`) use direct hex rather than SLDS tokens because they carry semantic meaning independent of the surface color — the tint IS the signal. Overriding these in dark mode requires explicit dark-mode rules, not token inheritance.

## Typography

**UI Font:** SF Pro Text (system-ui fallback) — matches macOS and Salesforce.
**Mono Font:** SF Mono (Menlo fallback) — job output, file paths, env keys only. Never used for UI copy.

### Hierarchy
- **Stat value:** 22px, 700 — reserved for counted quantities on the Files page stat strip. The largest text on any page.
- **Body / table cell:** 0.875rem (14px), 400, 1.5 line-height. The default register for everything.
- **Section header (h2 in cards):** 11px, 700, uppercase, 0.06em tracking, muted color. Introduces a card's content group.
- **Table column header (th):** 11px, 700, uppercase, 0.05em tracking, muted. Identifies columns in related-list tables.
- **Badge / label:** 11px, 700, uppercase, 0.04em tracking. The smallest text; conveys categorical status at a glance.
- **Mono / output:** 12px SF Mono, 1.6 line-height. Job output panel and source drawer content only.

### Named Rules
**The Mono Firewall.** SF Mono appears only for machine-generated or machine-readable text: job output, file paths, env key names, inline code tokens. Never use mono for UI copy, labels, or nav text.

## Layout

Single-column, `max-width: 1400px` (the Files page benefits from the width), `24px` horizontal padding. The header is a sticky strip (`position: sticky; top: 0; z-index: 10`) with a `4px` brand-blue bottom border. Nav links are horizontal with `6px` padding between items.

**Brief State layout:** A sticky tab bar sits immediately below the header (`top: 53px; z-index: 9`), separated from the header by 1px. Below the tabs is only the active panel. The overdue banner (when present) appears above the tab bar and renders regardless of active tab. The Today's Focus pinned card always renders at the top of the Act tab when items are pinned.

**Related-list tables:** Grey header row (`surface-2` background), `10px/12px` cell padding, `1px border-1` row dividers, `hover: rgba(1,118,211, 0.03)` tint on body rows. Actions are right-aligned in the last column using `display: flex; gap: 4px; justify-content: flex-end`. Overdue rows get a `rgba(194,57,52,0.04)` red tint; due-soon rows get `rgba(221,122,1,0.04)` orange tint; inbox rows get `#fffdf0` warm yellow tint.

**Settings page:** Two-column grid on the left + right, right side further split into a column of cards. `1fr 1fr` gap with `20px` gutter.

**Run page:** Action tiers separated by `border-top: 1px solid border-1` dividers. Each tier has a label (`ta-action-tier-label`: 11px uppercase muted) and one or more action groups. Daily > Periodic > Rare is the visual frequency hierarchy.

## Elevation & Depth

Primarily flat. The SLDS token system handles depth in light mode via surface color steps — card surfaces are `surface-1` on a `surface-1` page, differentiated only by their `1px border-1` border. No box-shadows on cards.

The **source drawer** (`#source-drawer`) and **render-warnings popover** (`ta-render-warn-popover`) are the only permitted shadow consumers, both using `var(--slds-g-shadow-2, 0 4px 16px rgba(0,0,0,.1))`. These are overlapping panels that must appear above the flat field — structural depth, not decoration.

**The Flat-By-Default Rule.** All surfaces are flat at rest. `--slds-g-shadow-2` is permitted only for elements that physically overlap the canvas: the source drawer (fixed side panel) and the render-warnings popover (anchored dropdown). Adding shadows elsewhere breaks the uniform depth contract.

## Shapes

The form language is gently, consistently rounded — deferring to the SLDS radius token scale.

- **`sm` radius** (`var(--slds-g-radius-border-1, 0.125rem)` ≈ 2px): Small buttons (`.ta-btn-sm`), date inputs, defer toggle buttons. The tightest round; used where space is at a premium.
- **`md` radius** (`var(--slds-g-radius-border-2, 0.25rem)` ≈ 4px): Standard buttons, badges, form inputs, flash messages, nav link hover states. The default interactive radius.
- **`lg` radius** (`var(--slds-g-radius-border-3, 0.5rem)` ≈ 8px): Cards, job-status panel, source drawer, stats tiles, overdue banner, render-warn popover. The container radius.
- **Toggle:** fully pill-shaped (`border-radius: 20px` on track, `border-radius: 50%` on thumb). Only component that breaks from the scale; the pill shape is a universally understood on/off affordance.
- **No hard edges.** Nothing uses `border-radius: 0`. Even the source drawer's left edge carries `lg` on its right corners via the containing `border-radius`.

**The Token Radius Rule.** All radius values reference the SLDS scale vars with a px fallback. Never hardcode a bare pixel value for `border-radius` on a standard element — the fallback is for environments where the SLDS stylesheet hasn't loaded.

## Components

### Header

Sticky strip at the top of every page. `background: surface-1`, `border-bottom: 4px solid brand (#0176d3)`. Contains: app name (14px 700), horizontal nav, optional quick-run strip (Brief State only), spacer, optional render-warn chip + shared-files link. The 4px blue bottom border is the only persistent brand expression.

### Nav

Horizontal link row. Default: `font-size: 0.875rem`, muted color, `6px 10px` padding, `md` radius. Hover/active: `surface-2` background, primary text. Active tab gets class `.active`. Focus: 2px blue outline. A count badge (`.ta-nav-badge`) in brand blue on a circular pill appears on Brief State when pending acts exist.

### Tabs (Brief State)

Horizontal tab bar sticky below the header (`top: 53px`). Each tab is a `<button role="tab">`:
- Default: 400 weight, muted color, transparent bottom border, `12px 20px` padding
- Hover: primary text color
- Active: 600 weight, brand-blue color, `3px` brand-blue bottom border (`margin-bottom: -1px` to overlap the container border)
- Count pill (`.ta-bs-tab-count`): `surface-2` bg + muted text for inactive; `#daeaf8` blue-tinted bg + brand text for active; `#fef1f1` + error text for urgent variant

Tab state is persisted to `sessionStorage` (key: `bs-active-tab`) and restored on load.

### Related-List Tables (`.ta-rl`)

The primary data display pattern for all Brief State panels.

- `thead tr` background: `surface-2`
- `th`: 11px uppercase muted, `1px border-2` bottom border
- `td`: `10px 12px` padding, `1px border-1` bottom border, `0.875rem` font
- Row hover: `rgba(1,118,211,0.03)` blue tint
- Overdue row (`.ta-rl-overdue td`): `rgba(194,57,52,0.04)` red tint
- Due-soon row (`.ta-rl-due-soon td`): `rgba(221,122,1,0.04)` orange tint
- Inbox row (`.ta-rl-inbox-row td`): `#fffdf0` warm yellow — distinct from overdue/due-soon

Row actions use `.ta-rl-actions`: `display: flex; gap: 4px; justify-content: flex-end`. Section-group header rows use `.ta-rl-section-header`: `surface-1` background, label style text, `1px border-1` bottom.

Keyboard navigation (Act tab only): `j/↓` next, `i/↑` prev, `k` done, `d` drop (confirm), `1/3/m` defer shortcuts. Focused row gets `.ta-bs-item-focused` — blue left border on first `td` + `rgba(1,118,211,0.05)` row tint.

### Cards

Default (`.ta-card`): `surface-1` background, `1px border-1` border, `lg` radius, `20px` padding. No shadow.

Variants:
- **Today's Focus** (`.ta-card` + inline `border: 2px solid brand; border-width: 2px`): pinned act items. The 2px blue border distinguishes it from the act table immediately below.
- **Inbox** (`.ta-card-inbox`): `#fffdf0` warm yellow background, warning-color border and h2. Signals items awaiting triage decision.

Card `h2` is label-style: 11px, 700, uppercase, 0.06em tracking, muted.

### Overdue Banner (`.ta-bs-brief-banner`)

Appears above the tab bar whenever there are overdue act items. `#fef1f1` background, `1px #fac9c9` border, `4px error-red` left accent border, `lg` radius. Contains an inline SVG alert icon (error red), a bold title line, and a bullet list of overdue item texts. Always visible regardless of active tab.

### Badges (`.ta-badge`)

Common shape: `display: inline-block; padding: 2px 7px; md radius; 11px 600 uppercase 0.04em tracking`.

Full vocabulary (light-mode tint / text, dark-mode gets deep tint / lighter text):

| Key | Background | Text |
|---|---|---|
| `exec` | `#fef1f1` | error red |
| `solution` | `#eaf1fb` | brand blue |
| `daily` | `#eaf5ec` | success green |
| `standup` | `#fff3e0` | warning orange |
| `notes` | `#f5eafb` | purple |
| `pulse` | `#eaf5ec` | success green |
| `delta` | `#eaf5f5` | teal |
| `synthesis` | `#f5eafb` | purple |
| `act` | `#fef1f1` | error red |
| `watch` | `#fff3e0` | warning orange |
| `delegate` | `#eaf1fb` | brand blue |
| `they-owe` | `#f5eafb` | purple |
| `insight` | `#eaf5f5` | teal |
| `pending` | `#eaf5ec` | success green |
| `complete` | `#eaf5ec` | success green |
| `deferred` | `#fff8e1` | `#8a6000` gold |
| `expired` | `#fef1f1` | error red |
| `dropped` | `surface-2` | muted |
| `overdue` | `#fef1f1` | error red + `1px #fac9c9` border + 700 weight — loudest |

### Buttons

Common base (`.ta-btn`): `inline-flex; align-items: center; gap: 6px; md radius; 8px 16px; 0.875rem 600; cursor: pointer; border: none; transition: opacity .15s; white-space: nowrap`.

- **Primary (`.ta-btn`):** Brand blue background, white text. `opacity: 0.85` on hover. The default highest-priority action.
- **Daily Pulse (`.ta-btn-daily-pulse`):** Same as primary — brand blue, white text. Semantically the trigger for the most frequent synthesis.
- **Secondary (`.ta-btn-secondary`):** `surface-1` bg, primary text, `1px border-2`. Hover: `surface-2` bg + `opacity: 1`.
- **Periodic (`.ta-btn-periodic`):** Same as secondary. For weekly/catch-up synthesis — second-tier frequency.
- **Rare (`.ta-btn-rare`):** `surface-1` bg, muted text, `1px border-2`. Hover: muted→primary text + `surface-2` bg. For career/timecard — infrequent, low visual weight.
- **Destructive (`.ta-btn-destructive`):** Error red background, white text. Hover: `#a61c17` darkened red + `opacity: 1`. Reserved for server restart and other irreversible administrative actions.
- **Small (`.ta-btn-sm`):** `3px 8px` padding, `11px` font, `sm` radius. Used in all table row actions and the header quick-run strip.
- **Completion actions (Done, Dismiss, Drop):** Always secondary — never red/destructive. Drop uses a confirm dialog. Completing an obligation is a neutral act.

All buttons: `outline: 2px solid brand; outline-offset: 2px` on `:focus-visible`.

### Flash Messages (`.ta-flash`)

`10px 16px` padding, `md` radius, `0.875rem`. Three variants:

- **OK (`.ta-flash-ok`):** `#eaf5ec` bg, success green text, `1px #c2e0c6` border.
- **Error (`.ta-flash-err`):** `#fef1f1` bg, error red text, `1px #fac9c9` border.
- **Info (`.ta-flash-info`):** `#eaf1fb` bg, brand blue text, `1px #b8d4f5` border. Used only for the job-running banner (not a warning or error — indicates active progress).

### Toggle Switch (`.ta-toggle`)

`36px × 20px` pill track. Track: `border-2` bg unchecked, brand blue checked, `border-radius: 20px`. Thumb: `14px × 14px` white circle, `border-radius: 50%`, `3px` offset from track edge, slides `16px` on check. `:focus-visible` on the hidden `<input>` shows a 2px blue outline on the slider.

### Form Inputs

Text/URL fields: `surface-1` bg, `1px border-2`, `md` radius, `primary text`, `0.875rem`, `7px 10px` padding, `100%` width. Focus: no outline, `border-color: brand`. Labels: 12px 600 uppercase 0.05em tracking muted.

Date inputs: `surface-1` bg, `1px border-2`, `sm` radius, `11px`, `3px 6px` padding. Focus: `border-color: brand`.

Select (inline form selects in table rows): secondary button appearance — `surface-1` bg, primary text, `1px border-2`, `sm` radius.

### Source Drawer (`#source-drawer`)

Fixed-position right panel: `540px` wide, `100vh` height, `surface-1` background, `1px border-1` left border, `z-index: 100`. Entry/exit animated via `transform: translateX(100%)` → `translateX(0)` with `0.2s ease`. Shadow: `var(--slds-g-shadow-2, -4px 0 16px rgba(0,0,0,.1))`. Header: `14px 20px` padding, `1px border-1` bottom, title in label style, close button secondary-small. Content: `pre` with SF Mono 12px, `overflow-y: auto`, `max-height` is the viewport. Closes on `Escape`.

### Render-Warn Chip (`.ta-render-warn-chip`)

Header-level warning indicator. `#fff3e0` bg, warning orange text, `1px #f5c26b` border, `md` radius, `3px 10px` padding, 11px 700 uppercase. Toggles a popover (`.ta-render-warn-popover`) on click/Enter: `surface-1` bg, `1px border-1`, `lg` radius, `var(--slds-g-shadow-2)`, `z-index: 100`, `min-width: 220px`. Popover items: 12px 400 normal weight.

### Job Panel (`#job-status`)

Appears below the action cards during a running job. `surface-2` bg, `1px border-1`, `lg` radius, `16px` padding. Heading in label style. Output in SF Mono 12px with `max-height: 320px; overflow-y: auto`. A `.ta-spinner` (12px circular border animation, brand-colored border-top) runs while the job is active.

### Stats Strip (`.ta-stats`)

Flex row of `.ta-stat` tiles: `surface-1` bg, `1px border-1`, `lg` radius, `14px 20px` padding, `min-width: 140px`. Stat value: 22px 700 (largest text in the app). Label: 11px muted uppercase 0.05em tracking.

## Do's and Don'ts

### Do:
- **Do** use `var(--slds-g-color-brand-2, #0176d3)` for all interactive affordances: buttons, tab underlines, focus rings, count pills on active tabs.
- **Do** keep the Brief State tab bar sticky (`top: 53px`) so it's reachable without scrolling past the header.
- **Do** tint overdue rows with `rgba(194,57,52,0.04)` — the row tint is visible at a glance without requiring a badge scan.
- **Do** use related-list table layout (grey header, row hover, right-aligned actions) for all Brief State content panels.
- **Do** persist the active tab to `sessionStorage` so returning to the page resumes context.
- **Do** keep the overdue banner above the tabs — it's the only element that renders regardless of tab state.
- **Do** tier synthesis buttons by frequency: Daily Pulse is primary blue; Weekly/Catch-up are secondary; Career/Timecard are rare (muted secondary).
- **Do** use `var(--slds-g-shadow-2)` only for the source drawer and render-warn popover — elements that physically overlap the canvas.
- **Do** preserve hardcoded hex values for badge backgrounds and the inbox card; these semantic tints must not be inherited from SLDS surface tokens.

### Don't:
- **Don't** stack Act + Watch + Delegate vertically. The tab model is the answer to paralysis — mutually exclusive panels, not a vertical scroll.
- **Don't** use red or destructive styling for completion or dismissal actions (Done, Dismiss, Drop). Those are secondary buttons.
- **Don't** use emoji as icons. The sole exception is the Unicode braille dot (⠿) drag handle in the program order list.
- **Don't** add box-shadows to cards. Flat surfaces differentiated by border only.
- **Don't** load dark mode by default. `slds-color-scheme_dark` is applied only when `localStorage.getItem('slds-ui-color-mode') === 'dark'`.
- **Don't** use SF Mono for UI copy, labels, nav, or badge text. Mono is strictly for machine-generated / machine-readable content.
- **Don't** invent a new hue for a new badge type. Extend the existing semantic vocabulary: red for urgency, orange for deferred/inbox, blue for delegation, purple for interpersonal, teal for insight, green for success.
