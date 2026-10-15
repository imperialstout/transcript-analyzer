# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Brad Gross — sole user. Revenue Cloud CTO running a large enterprise program (Siemens SherpaX). Opens the dashboard on macOS (Chrome/Safari) at localhost:7070, multiple times per day, between meetings and at end of day.

## Product Purpose

Personal command center for a meeting-heavy executive. The tool processes raw meeting transcripts and notes into structured analyses, surfaces action items and commitments, and synthesizes across a day/week/career horizon into a coherent operational picture. The dashboard is the control surface for that pipeline: trigger runs, review what landed, manage the triage backlog, and drive synthesis. Success means Brad walks into the next meeting knowing exactly what he committed to and what changed.

## Positioning

A private intelligence layer that sits between calendar chaos and executive decision-making. Unlike a general note-taking tool, it routes transcripts through tiered Claude analysis (EXEC vs. SOLUTION vs. DAILY tier, with Opus on strategic content), maintains a persistent program reference, and produces both an internal political read and a sanitized shareable version — automatically. The "Brief State" triage loop (pending acts, they-owe, watches) is the core differentiation: it's an obligation ledger, not a feed.

## Operating Context

- Runs as a launchd daemon on the work machine; UI-driven on demand on the personal machine.
- Content lives in Google Drive (`Workcall/`), not the repo.
- Transcripts arrive from Plaud (voice), Gemini (meeting notes), and manual drops.
- The pipeline runs on a 30-minute scheduled cadence (work) or on demand (personal).
- Brad accesses the dashboard between back-to-back meetings; every interaction must be fast and scannable — no waiting, no context loss.
- Output is consumed in two ways: the dashboard itself (live status, triage) and the Analyzed/ files in Drive (read directly or shared).

## Capabilities and Constraints

- Flask app at localhost:7070; no authentication, no multi-user, no cloud deployment.
- Routes: Home (file table + stats + synthesis actions), Brief State (obligation ledger), Slack Paste (manual input), Settings (.env editor), plus internal job-status and alert APIs.
- One background job at a time (threading.Lock); synthesis streams live to the job panel.
- Dark theme only — this is a tool used in a dimly lit office/home setting.
- No mobile requirement; desktop macOS only.
- No test suite; runtime behavior is the contract.
- The HTML is rendered as a single inline template string in ui.py — no separate template files or build step.

## Brand Commitments

Name: "Transcript Analyzer" — functional workhorse name, no branding ambition. No logo, no external presence, no marketing surface.

## Evidence on Hand

- Full source in `/Users/brad.gross/code/transcript-analyzer/analyzer/ui.py` — the entire UI is a single Python file (~1100 lines), inline HTML template with Jinja2 variables.
- Existing dark palette: `--bg: #0f1117`, `--surface: #1a1d27`, `--border: #2a2d3a`, `--accent: #4f8ef7`, `--green: #3ecf8e`, `--orange: #f7a24f`, `--red: #f76f6f`.
- No external CSS framework; all styles are inline in the template.
- No assets or logo files exist.

## Product Principles

1. **Speed over ceremony.** Brad is between meetings. Every click must do the thing immediately — no confirmation screens for safe actions, no multi-step flows for common triggers.
2. **Obligation-first.** The Brief State is the hero surface. Pending acts and overdue commitments must be impossible to ignore.
3. **Signal over noise.** Triage alerts and synthesis outputs earn attention by being specific and actionable — no padding, no hedging.
4. **Single-surface fidelity.** The whole tool is one dark page. Visual hierarchy carries navigation; no tabs, no modals, no distractions.
5. **Personal-grade craft.** This is a solo tool but Brad is an executive who cares about craft — the UI should feel considered, not hacked together.
