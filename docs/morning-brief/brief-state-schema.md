# Brief State Schema

File: `~/.cache/transcript-analyzer/brief-state.json`

Top-level structure: a JSON array of item objects. Newest items appended; sorted by `created_at` descending for reads.

---

## Item object

```json
{
  "id": "sha1-12char",
  "text": "one-line label matching the triage queue entry",
  "type": "act | watch | delegate | they-owe | insight",
  "owner": "Brad | person-name",
  "deadline": "2026-10-13 | null",
  "source_brief": "2026-10-12T19-47-03 - Morning Brief - 2026-10-12 [MORNING BRIEF].md",
  "source_pulse": "2026-10-12T19-23-33 - Daily Pulse - 2026-10-12 [DAILY PULSE].md",
  "status": "pending | complete | deferred | expired | dropped",
  "created_at": "2026-10-12T19:50:00Z",
  "closed_at": "2026-10-12T21:30:00Z | null",
  "deferred_to": "2026-10-13 | null",
  "notes": "optional context from triage reply or EOD close",
  "resurfaced_count": 0
}
```

---

## Field definitions

| Field | Type | Description |
|---|---|---|
| `id` | string | SHA-1 of `source_brief + text`, truncated to 12 chars. Deterministic — same item same brief = same id. |
| `text` | string | The one-line label from the triage queue (the bolded first line). |
| `type` | enum | See type definitions below. |
| `owner` | string | `"Brad"` for act items. Person name for delegate/they-owe. |
| `deadline` | ISO date or null | Explicit deadline if stated during triage. For they-owe items, the commitment date from the pulse. |
| `source_brief` | string | Filename of the `[MORNING BRIEF]` file that created this item. |
| `source_pulse` | string | Filename of the `[DAILY PULSE]` that surfaced this item. |
| `status` | enum | See status definitions below. |
| `created_at` | ISO datetime (UTC) | When this item was written to the store. |
| `closed_at` | ISO datetime (UTC) or null | When status moved to complete/deferred/expired/dropped. |
| `deferred_to` | ISO date or null | If status=deferred, the target date for resurfacing. |
| `notes` | string | Brad's inline notes from triage reply (e.g. `act (send Bill the consent language)`) or EOD close comment. |
| `resurfaced_count` | int | How many times this item has reappeared as [OVERDUE] in a brief. Escalation signal. |

---

## Type definitions

| Type | Meaning |
|---|---|
| `act` | Brad owns it, action due today or by a named date. |
| `watch` | Monitoring only — no action needed today. Auto-expires after 5 business days with no pulse mention. |
| `delegate` | Brad owns the outcome but handed execution to a named person. Auto-prompts re-confirmation at 7 days. |
| `they-owe` | Someone else committed to a deliverable with a deadline. Brad's job is to follow up if it doesn't land. |
| `insight` | From the pulse's Private Read section. No action — framing for how Brad should handle related items. Expires end of day. |

---

## Status definitions

| Status | Meaning |
|---|---|
| `pending` | Open, not yet resolved. |
| `complete` | Brad confirmed done (via EOD close or next day's brief confirmed no follow-up needed). |
| `deferred` | Brad explicitly pushed to a future date. Resurfaces as act on that date. |
| `expired` | Passed deadline with no action and no deferral. Logged for accountability; resurfaces as [OVERDUE]. |
| `dropped` | Triaged as `drop` — no longer relevant. Never resurfaces. |

---

## ID generation

```python
import hashlib

def brief_item_id(source_brief: str, text: str) -> str:
    return hashlib.sha1(f"{source_brief}|{text}".encode()).hexdigest()[:12]
```

Same logic as `alerts.py` — consistent pattern across the codebase.

---

## Expiry rules (enforced by brief_state.expire_stale())

| Type | Rule |
|---|---|
| `act` | Deadline passed (or created_at + 1 business day if no deadline) → status=expired |
| `watch` | No pulse mention for 5 business days → status=expired; resurfaces in next brief |
| `delegate` | Age > 7 days, status=pending → resurface in brief as confirmation prompt |
| `they-owe` | Deadline passed, status=pending → promote to act item for Brad with text "Follow up: [original text]" |
| `insight` | Age > 1 day → status=expired (insights are day-specific context) |

---

## Example store (2 items)

```json
[
  {
    "id": "a3f8c21d04b1",
    "text": "Product-gap decision framework",
    "type": "act",
    "owner": "Brad",
    "deadline": "2026-10-13",
    "source_brief": "2026-10-12T19-47-03 - Morning Brief - 2026-10-12 [MORNING BRIEF].md",
    "source_pulse": "2026-10-12T19-23-33 - Daily Pulse - 2026-10-12 [DAILY PULSE].md",
    "status": "pending",
    "created_at": "2026-10-12T19:50:00Z",
    "closed_at": null,
    "deferred_to": null,
    "notes": "needs more context; find transcript section tonight before drafting",
    "resurfaced_count": 0
  },
  {
    "id": "b72e1a9f0c43",
    "text": "Erwan pre/post hook architecture session",
    "type": "they-owe",
    "owner": "Erwan",
    "deadline": "2026-10-17",
    "source_brief": "2026-10-12T19-47-03 - Morning Brief - 2026-10-12 [MORNING BRIEF].md",
    "source_pulse": "2026-10-12T19-23-33 - Daily Pulse - 2026-10-12 [DAILY PULSE].md",
    "status": "pending",
    "created_at": "2026-10-12T19:50:00Z",
    "closed_at": null,
    "deferred_to": null,
    "notes": "Erwan committed Oct 8 to book this week",
    "resurfaced_count": 0
  }
]
```
