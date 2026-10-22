# LDS Redesign Handover — Branch: UI-Redesign-Salesforce-LDS

## What's done
1. CDN `<link>` added to `_HTML` head: `@salesforce-ux/design-system-2@2.0.0/dist/css/bundled/slds2.cosmos.css`
2. Dark mode toggle: inline `<script>` in `<head>` + `toggleTheme()` JS function + ☀/☾ button in header. Persists to `localStorage`.
3. Entire old `<style>` block (~333 lines) replaced with new SLDS-based delta CSS using `ta-*` namespace and `--slds-g-*` hooks with fallback values.
4. `<header>` and `<nav>` HTML updated to `ta-header` / `ta-nav` classes.
5. JS keyboard nav updated: `bs-item-focused` → `ta-bs-item-focused`.
6. JS drag-reorder updated: `bs-item`, `bs-group-items` → `ta-bs-item`, `ta-bs-group-items`.
7. `_HOME_BODY` fully updated.
8. `_SETTINGS_BODY` fully updated.
9. `_RUN_BODY` fully updated.
10. `_BRIEF_STATE_BODY` partially updated (flash/banner sections done, in-progress on item rows).

## What remains (~149 old class refs)
Run this to see them:
```bash
grep -n 'class="bs-\|class="badge \|class="btn-\|class="card\|class="spinner\|class="quick-link\|class="muted\|class="sr-only\|class="drag-handle\|class="section-title\|class="action-tier\|class="paste-area\|class="toggle\|class="field\|class="prep-\|class="kb-hint\|class="nav-badge\|class="code-inline\|class="inline-program\|class="pulse-\|class="weekly-nudge' analyzer/ui.py
```

### Class name mapping (old → new)
| Old | New |
|---|---|
| `bs-item` | `ta-bs-item` |
| `bs-body` | `ta-bs-body` |
| `bs-text` | `ta-bs-text` |
| `bs-meta` | `ta-bs-meta` |
| `bs-notes` | `ta-bs-notes` |
| `bs-actions` | `ta-bs-actions` |
| `bs-defer-sep` | `ta-bs-defer-sep` |
| `bs-empty` | `ta-bs-empty` |
| `bs-item-overdue` | `ta-bs-item-overdue` |
| `bs-item-due-soon` | `ta-bs-item-due-soon` |
| `bs-program-header` | `ta-bs-program-header` |
| `bs-program-header-toggle` | `ta-bs-program-header-toggle` |
| `bs-group-section` | `ta-bs-group-section` |
| `bs-section` | `ta-bs-section` |
| `bs-section-chevron` | `ta-bs-section-chevron` |
| `bs-group-items` | `ta-bs-group-items` |
| `bs-brief-banner-title` | `ta-bs-brief-banner-title` |
| `bs-brief-banner-items` | `ta-bs-brief-banner-items` |
| `bs-item-urgent` | `ta-bs-item-urgent` |
| `drag-handle` | `ta-drag-handle` |
| `inline-program-form` | `ta-inline-program-form` |
| `badge badge-*` | `ta-badge ta-badge-*` |
| `badge-overdue` | `ta-badge-overdue` (standalone) |
| `btn-secondary btn-sm` | `ta-btn ta-btn-secondary ta-btn-sm` |
| `btn-destructive btn-sm` | `ta-btn ta-btn-destructive ta-btn-sm` |
| `btn-secondary` | `ta-btn ta-btn-secondary` |
| `card` | `ta-card` |
| `card card-inbox` | `ta-card ta-card-inbox` |
| `quick-link` | `ta-quick-link` |
| `muted` | `ta-muted` |
| `sr-only` | `ta-sr-only` |
| `kb-hint` | `ta-kb-hint` |
| `nav-badge` | `ta-nav-badge` |
| `code-inline` | `ta-code-inline` |
| `prep-bullet` | `ta-prep-bullet` |
| `prep-controls` | `ta-prep-controls` |
| `paste-area` | `ta-paste-area` |
| `pulse-freshness` | `ta-pulse-freshness` |
| `pulse-green/orange/red/gray` | `ta-pulse-green/orange/red/gray` |
| `weekly-nudge` | `ta-weekly-nudge` |
| `weekly-nudge-orange/muted` | `ta-weekly-nudge-orange/muted` |
| `ops-drawer` | `ta-ops-drawer` |
| `ops-chevron` | `ta-ops-chevron` |
| `header-quick-run` | `ta-header-quick-run` (done in header HTML) |
| `spinner` | `ta-spinner` |

### Inline CSS var replacements needed
| Old | New |
|---|---|
| `var(--surface)` | `var(--slds-g-color-surface-1,#fff)` |
| `var(--border)` | `var(--slds-g-color-border-1,#e5e5e5)` |
| `var(--accent)` | `var(--slds-g-color-brand-2,#0176d3)` |
| `var(--red)` | `var(--slds-g-color-error-2,#c23934)` |
| `var(--green)` | `var(--slds-g-color-success-2,#2e844a)` |
| `var(--gold)` | `var(--slds-g-color-warning-2,#dd7a01)` |
| `var(--muted)` | `var(--slds-g-color-on-surface-4,#706e6b)` |
| `var(--bg-alt)` | `var(--slds-g-color-surface-2,#f3f3f3)` |
| `var(--fg-muted)` | `var(--slds-g-color-on-surface-4,#706e6b)` |
| `var(--bg-alert)` | `#fff5f5` |

## Strategy for remaining work
Use `replace_all` mode in Edit tool for each mapping above to batch-replace throughout the file.
After all class names are updated, run the app:
```bash
source ~/.venvs/transcript-analyzer/bin/activate
python -m analyzer ui --port 7071
```
Verify all pages at http://localhost:7071.
