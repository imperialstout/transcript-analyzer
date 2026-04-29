# Transcript Analyzer — Solution Doc

**Audience:** Claude Code, planning a build.
**Author:** Brad (via Claude in chat).
**Status:** Planning input, not a spec. Pseudo-code is illustrative; iterate on structure as needed.

---

## 1. Problem in one paragraph

Brad runs an enterprise Salesforce program (SherpaX at Siemens) and produces meeting transcripts daily via Plaud and other recording sources. Today, transcripts land in a Google Drive folder via a Zapier zap. Analysis happens by Brad pasting transcripts into Claude.ai chat sessions and naming a prompt from his prompt library. The chat-based workflow is hitting context-window limits during heavy weeks (10–25 transcripts). The analyses are filed back to a separate Drive folder using a strict filename convention. He wants to move analysis out of chat and into a script that runs unattended, while keeping Drive as the source and destination so weekly cross-transcript analysis (done in Claude.ai) still works the same way.

## 2. Goals

- **Move per-transcript analysis out of the chat context window.** Each analysis should be its own clean Anthropic API call.
- **Keep Google Drive as source and destination.** Transcripts in folder ID `1shR1BOXTiDWhMFcQQ1KDu2vAYjEvpPea`; analyses written to folder ID `16oPdYDeV9DZ9BkaD5iMz0u4yKlZXKo4L`.
- **Auto-route to the right prompt** using a cheap classification API call. Brad does not want to manually pick prompts.
- **Preserve filing convention exactly** — see section 6.
- **Be runnable manually first, cron'd later, with a clean upgrade path to a server.** Don't over-engineer day one.

## 3. Non-goals

- Personal-content stripping. Brad will manage what he uploads or delete analyses he doesn't want.
- Cross-transcript synthesis (C1 Weekly Drift Scan, C2 Stakeholder Trajectory). Those continue to happen in Claude.ai chat against the analyzed folder.
- A polished UI. CLI is fine for v1.
- Real-time processing. Batch is fine. Even "once a day" is fine.

## 4. Desired experience

The user experience Brad is optimizing for:

**Manual mode (v1):**
```
$ analyze
Found 7 unanalyzed transcripts.
  [1/7] 2026-04-28 SI RCA Steerco.txt → routed to A1 (Client Signal)
  [2/7] 2026-04-28 1-1 with Christoph.txt → routed to A3 (1:1 & Team Pulse)
  [3/7] 2026-04-28 CML Escalation.txt → routed to B2 (Escalation & Issue)
  ...
Analyzing in parallel (max 3 concurrent)...
  [1/7] done — filed as "2026-04-28T14-30-00 - SI RCA Steerco - 2026-04-28 [ANALYZED].txt"
  [2/7] done — filed as ...
  ...
All 7 analyses complete. 0 errors.
```

**Cron mode (v2):** same, but silent unless something fails. On failure, write an error file to a third Drive folder or send Brad an email — pick whichever is easier.

**What "unanalyzed" means:** the script needs a way to know which transcripts have already been processed. Options listed in section 7.

## 5. Architecture

**Critical assumption: Google Drive for Desktop is installed and syncing both folders to local paths.** This eliminates the need for the Drive API entirely. The script reads and writes local files; the sync client handles the round trip to Drive.

```
   ┌────────────────────┐         ┌────────────────────┐
   │  Google Drive      │ ◄─────► │  Local filesystem  │
   │  Call Transcripts  │  sync   │  ~/.../Call Trans..│
   │  Analyzed          │ ◄─────► │  ~/.../Analyzed    │
   └────────────────────┘         └─────────┬──────────┘
                                            │ read/write
                                            ▼
                            ┌──────────────────────────────────┐
                            │  analyzer (Python script)        │
                            │  ┌────────────────────────────┐  │
                            │  │ 1. List local transcripts  │  │
                            │  │ 2. Skip ones with a        │  │
                            │  │    matching analysis file  │  │
                            │  │ 3. For each new file:      │  │
                            │  │    a. Read text            │  │
                            │  │    b. Route → which prompt │  │
                            │  │    c. Run analysis prompt  │  │
                            │  │    d. Write to Analyzed/   │  │
                            │  └────────────────────────────┘  │
                            └──────────────────────────────────┘
```

**Key components:**

- **Filesystem I/O.** `os.listdir`, `open().read()`, `open().write()`. No Drive API, no auth.
- **Anthropic client.** Two call types: routing (cheap, fast) and analysis (the real work).
- **Prompt library loader.** Reads prompt definitions from local files in the repo.
- **Context grounding.** The Program Context Brief is a system-prompt-level input on every analysis call. Lives in the local repo.
- **State tracker.** Filename matching against the local Analyzed directory.

**Why this works:** Drive for Desktop syncs new transcripts down within seconds of the Zap dropping them. The script runs against local files (fast, simple, no quotas). The script writes new analyses locally; sync pushes them up to Drive. From Claude.ai's perspective, the Analyzed folder fills up the same way it does today.

**Trade-off accepted:** Sync is near-real-time but not instantaneous. If the script runs <10 seconds after a transcript lands in Drive, the local copy may not exist yet. Mitigation: cron at intervals (every 5-15 min), or just accept that "missed this run, picks it up next run" is fine.

**Future server migration:** Drive for Desktop doesn't run headless on Linux. If/when this moves to a server, swap the local-FS module for a Drive API module. Architecturally clean — only one module changes.

## 6. Filing convention (exact, do not deviate)

This is established and the weekly analysis depends on it.

**Filename:** `[ISO-timestamp] - [Original Title] - [Date] [ANALYZED]`

- ISO timestamp = the analysis run time, e.g. `2026-04-28T14-30-00` (note: colons in ISO times don't play well with filesystems; use hyphens in the time portion).
- Original Title = the source transcript's filename minus extension.
- Date = the date of the meeting, parsed from the source filename if it starts with a date, otherwise today.
- `[ANALYZED]` is literal.

**File format:**
- Plain UTF-8 text files. `.txt` extension.
- The Drive auto-conversion-to-Google-Doc concern from the old chat-based workflow doesn't apply here — Drive for Desktop syncs files as-is. They appear in Drive as plain text files, which is exactly what we want for Claude.ai weekly analysis to read them cleanly.

## 7. State tracking — how to know what's already analyzed

Filename matching against the local Analyzed directory.

Before processing transcript `2026-04-28 SI RCA Steerco.txt`, list the local Analyzed directory and check whether any file contains `SI RCA Steerco - 2026-04-28` in its name. If yes, skip.

Pros: stateless, fast (local FS), no external dependencies.
Cons: a rename in either folder breaks the match. That's a Brad-discipline issue, not a script issue.

If this gets unreliable, a `.processed.json` manifest in the script's working directory is a clean fallback. Don't build it day one.

## 8. Prompt routing — the cheap classifier call

Before each analysis, make a small API call to pick the prompt. Use a fast/cheap model (Haiku-class). Input: the first ~2000 tokens of the transcript plus a short list of prompt options with one-line descriptions. Output: the prompt key (e.g., `A1`, `B2`).

**Pseudo-code:**

```python
def route_prompt(transcript_text: str) -> str:
    """Returns a prompt key like 'A1', 'A2', 'B2' etc."""
    options = """
    A1 - Client Signal: client-facing meetings, steering, governance
    A2 - Decision & Direction: internal arch/design sessions where decisions get made
    A3 - 1:1 & Team Pulse: 1:1s, standups, people-focused
    B1 - Discovery & Requirements: discovery sessions, requirements workshops
    B2 - Escalation & Issue: something is on fire, post-incident, firefight
    B3 - What Did We Actually Commit To: commitment-focused after client meeting
    B4 - Political Read: client meeting, want only the dynamics
    """
    
    sample = transcript_text[:8000]  # rough cap
    
    response = anthropic.messages.create(
        model="claude-haiku-4-5",
        max_tokens=10,
        system="You classify meeting transcripts into one of the prompt keys provided. Respond with ONLY the key (e.g. 'A1'). No explanation.",
        messages=[{
            "role": "user",
            "content": f"Prompt options:\n{options}\n\nTranscript excerpt:\n{sample}\n\nWhich prompt key fits best?"
        }]
    )
    return response.content[0].text.strip()
```

**Failure mode:** if the classifier returns garbage or an unknown key, default to A2 (Decision & Direction). Log the routing decision.

## 9. Analysis call — the real work

```python
def analyze(transcript_text: str, prompt_key: str) -> str:
    prompt_body = load_prompt(prompt_key)  # from local prompt library
    context_brief = load_context_brief()   # the program context brief markdown
    
    system_prompt = f"""You are analyzing meeting transcripts for an enterprise Salesforce program (SherpaX at Siemens).

The user is Brad, Revenue Cloud CTO. Posture: attributed, specific, non-neutralized. Don't sanitize. Use the Program Context Brief below to ground who's who.

PROGRAM CONTEXT BRIEF:
{context_brief}

PROMPT TO EXECUTE:
{prompt_body}
"""
    
    response = anthropic.messages.create(
        model="claude-opus-4-7",  # or whatever current best model is
        max_tokens=8000,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"Transcript:\n\n{transcript_text}"
        }]
    )
    return response.content[0].text
```

**Notes:**
- Don't truncate the transcript. If a transcript is too long for the model's context, that's a real problem worth surfacing — but it's rare. Plaud transcripts of 60-min meetings are usually well under 50k tokens.
- The system prompt does the heavy lifting. The user message is just the transcript.
- Token usage will be substantial because the context brief is long. That's fine; it's the price of grounded output.

## 10. Putting it together — the main loop

```python
def main():
    transcripts = list_local_dir(CALL_TRANSCRIPTIONS_PATH)
    analyzed = list_local_dir(ANALYZED_PATH)
    
    unanalyzed = [t for t in transcripts if not is_analyzed(t, analyzed)]
    
    print(f"Found {len(unanalyzed)} unanalyzed transcripts.")
    
    for i, transcript_path in enumerate(unanalyzed, 1):
        try:
            text = read_file(transcript_path)
            prompt_key = route_prompt(text)
            print(f"  [{i}/{len(unanalyzed)}] {transcript_path.name} → routed to {prompt_key}")
            
            analysis = analyze(text, prompt_key)
            
            output_filename = build_filename(transcript_path.name)
            output_path = ANALYZED_PATH / output_filename
            write_file(output_path, analysis)
            
            print(f"  [{i}/{len(unanalyzed)}] done — filed as {output_filename}")
        except Exception as e:
            print(f"  [{i}/{len(unanalyzed)}] FAILED: {e}")
            # log, continue, don't crash the batch

if __name__ == "__main__":
    main()
```

**Concurrency:** v1 can be sequential. If batches get big, parallelize with a small worker pool (3 concurrent is safe against API rate limits).

**Sync timing:** files written to the local Analyzed directory are picked up by Drive for Desktop and uploaded within seconds. The script does not need to wait for or verify the sync.

## 11. Configuration

A `.env` file or similar for:
- `ANTHROPIC_API_KEY`
- `CALL_TRANSCRIPTIONS_PATH` — local path where Drive for Desktop syncs the Call Transcriptions folder. Brad will set this to whatever his local mount looks like (e.g., `~/Library/CloudStorage/GoogleDrive-brad@.../My Drive/.../Call Transcriptions` on Mac).
- `ANALYZED_PATH` — local path for the Analyzed folder.
- `PROMPTS_DIR` — path to the local prompt library (in the repo).
- `CONTEXT_BRIEF_PATH` — path to the local Program Context Brief (in the repo).

**No Google auth needed.** Drive for Desktop is already authenticated; the script just reads and writes local files.

**Drive sync prerequisite:** Brad needs to confirm Drive for Desktop is installed and that both folders are syncing. On Mac, "Stream files" mode is fine — files are downloaded on demand. "Mirror files" mode also works. The script doesn't care which mode as long as the files are accessible at the configured paths.

## 12. Repo structure (suggested)

```
transcript-analyzer/
├── analyzer/
│   ├── __init__.py
│   ├── main.py              # entry point
│   ├── filesystem.py        # local FS read/write (replaceable with drive.py later)
│   ├── anthropic_client.py  # routing + analysis calls
│   ├── prompts.py           # prompt library loader
│   └── filing.py            # filename construction
├── prompts/
│   ├── A1_client_signal.md
│   ├── A2_decision_direction.md
│   ├── A3_one_on_one.md
│   ├── B1_discovery.md
│   ├── B2_escalation.md
│   ├── B3_commitments.md
│   └── B4_political_read.md
├── context/
│   └── program_context_brief.md
├── .env.example
├── requirements.txt
└── README.md
```

The `prompts/` and `context/` directories let Brad edit the library and brief in his normal editor and have changes pick up on the next run. The `filesystem.py` module is the single place to swap for a `drive.py` module if the script ever moves to a headless server.

## 13. Build order — what to ship in what order

**v1 (this session, hopefully):**
- Local FS read (list + read file).
- Local FS write (with the right filename convention).
- Anthropic analysis call with hardcoded prompt (just A2 to start).
- Manual single-file mode: `analyze <local_file_path>` runs end-to-end on one file.

**v2:**
- Folder mode: `analyze` processes all unanalyzed files in the configured directory.
- Filename-based "is this analyzed already" check.

**v3:**
- Routing classifier call.
- Multiple prompts wired in.

**v4:**
- Concurrency (3-worker pool).
- Better error handling and logging.

**v5+:**
- Cron-friendly silent mode.
- Maybe a tiny web UI for re-running specific files with a different prompt if routing was wrong.
- Server migration (replace `filesystem.py` with a `drive.py` module that uses the Drive API).

The path to v1 is meaningfully shorter now that there's no Drive auth to wire up.

## 14. Open questions for Claude Code

1. **Anthropic SDK version.** Use the current Python SDK. Verify model names and API shape against current docs — these change. Don't assume the model strings in this doc are still current.
2. **Concurrency primitive.** `asyncio` vs. `concurrent.futures.ThreadPoolExecutor`. Threads are probably enough — this is I/O bound on API calls.
3. **Logging.** Stdout for v1, structured logs (JSON to a file) by v4.
4. **Path-handling library.** `pathlib` is the modern choice and handles Mac vs. Windows path differences cleanly — important since Drive for Desktop paths look different on each OS.

## 15. What this doc does not specify (intentionally)

- Test strategy. Manual eyeball testing for v1 is fine; add unit tests around filing.py and prompts.py once the shape stabilizes.
- Error handling beyond "log and continue." Failures will tell us what we need.
- Retry logic. The Anthropic SDK has built-in retries; trust them for v1.
- Rate limiting. We're nowhere near limits at 25 transcripts a week.
- Notification on failure. v1 prints to stdout; v2+ can email or Slack.

## 16. Definition of done for v1

Brad can run `analyze` on his laptop (with Drive for Desktop already syncing both folders) and have it pick up new transcripts from the local Call Transcriptions path, analyze them with a default prompt (A2), and write them to the local Analyzed path with the correct naming convention. Drive for Desktop syncs the result up to Drive automatically. One end-to-end success against a real transcript and v1 ships.
