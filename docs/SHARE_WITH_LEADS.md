# Turn your meeting transcripts into shareable summaries

A small tool that reads your meeting transcripts, writes a high-quality analysis of
each, and produces a **second, shareable version** with the internal/political/
career notes stripped out — so you can pass the clean version to peers and keep the
candid one for yourself. It runs on **your** work Claude seat, so it costs you nothing
personally.

You point it at your own meetings, your own people, and your own prompts. The clean
`[SHAREABLE]` summaries are also what you'd send back to Brad to give him signal across
the program without anyone re-reading every transcript.

---

## What you need (~15 minutes, one time)

- A **Mac**.
- A **work Claude Code seat** (the `claude` command, signed in with your work account).
  If you can run `claude` in a terminal and it works, you're set.
- Comfort **pasting a few commands into Terminal**. You won't need to understand them —
  a setup script checks everything and tells you exactly what (if anything) to install.

> The only things you might have to install yourself are **Homebrew** (one paste, it
> tells you) and the **Claude Code app/CLI**. The setup script detects what's missing
> and prints the exact command — you won't be hunting around.

---

## Part 1 — Get it running

Open **Terminal** and run these. (Type them or paste them at the prompt — but when a
step says "run a script," *run* it, don't paste the script's contents.)

```bash
# 1. Get the code (you'll sign into GitHub once if asked)
cd ~                      # or wherever you keep projects — NOT iCloud/Desktop
git clone <REPO_URL>      # Brad will give you this link
cd transcript-analyzer

# 2. Run setup — it checks prerequisites and sets up everything safe automatically
bash bin/setup.sh
```

`setup.sh` will either say **[ok]** down the line, or stop with a **[fix]** telling you
the one thing to install (e.g. Homebrew or Claude Code) — install it, then run
`bash bin/setup.sh` again. Repeat until it ends with "You're set."

---

## Part 2 — Make it yours

The tool reads two things you control, kept in your Google Drive (not in the code):

1. **Your prompts** — what kind of analysis to produce. Copy the starters from
   `docs/prompt_starters.md` into a file called `PromptLibrary.md` in your Drive, and
   tweak the wording to fit how *you* think about your meetings. There are five:
   `DAILY`, `STANDUP`, `SOLUTION`, `EXEC` (which kind of meeting), and `REDACT` (what to
   strip for the shareable version).
2. **Your context brief** — a short `Program_Context_Brief.md` in your Drive listing
   your people, who's who, and anything the tool should know to get names and dynamics
   right. (This file stays private to you — it never leaves your Drive.)

Then open your config file and point it at your stuff:

```bash
open -e ~/.config/transcript-analyzer/.env
```
Set the `*_PATH` lines to where your transcripts live and where you want output. The
setup script already filled in the Claude settings for you.

> **The shareable split works because of one convention:** each prompt ends with a
> `## Private read — internal only` section for the candid stuff, and the `REDACT` step
> removes that whole section. Keep that heading as-is and redaction stays reliable.

---

## Part 3 — Use it

- Drop a transcript (`.txt` or `.md`) into your transcripts folder.
- Run it:
  ```bash
  bash bin/smoke_test.sh        # safe dry-run on a sample, to confirm it all works
  ~/.venvs/transcript-analyzer/bin/python -m analyzer   # the real run on your files
  ```
- For each transcript you get two files in your `Analyzed/` folder: an `[ANALYZED]`
  (your candid version) and a `[SHAREABLE]` (clean, peer-safe).
- **Always read your first `[SHAREABLE]` file before sending it to anyone** — confirm it
  kept the substance and cut what you wanted cut. Adjust your `REDACT` prompt if not.

(Brad can help you set it to run automatically on a schedule once you're comfortable.)

---

## Part 4 — Share back

Send Brad your `[SHAREABLE]` summaries (a shared Drive folder is easiest). That gives him
a clean, consistent read across workstreams without anyone having to re-read raw
transcripts — and you keep your private `[ANALYZED]` versions to yourself.

---

## If something goes wrong

- **`setup.sh` stops with `[fix]`** — that's by design. Install the one thing it names,
  re-run `bash bin/setup.sh`.
- **`pip install` fails on the managed laptop** — usually a corporate proxy/cert. The
  script prints the workaround; if stuck, ask IT for the internal pip index URL.
- **"claude not found" or the seat check fails** — run `claude` once and `/login` with
  your work account, then re-run setup.
- **A command errors with "no such user or named directory"** — a stray space or missing
  `/` after `~`. Re-type it carefully (or use `$HOME` instead of `~`).
- **You pasted a script and got a wall of errors** — don't paste script *contents*; run
  them with `bash bin/<script>.sh`.
- **Anything else** — send Brad the exact terminal output (an easy way: open a GitHub
  issue and paste it there).

---

*Costs you nothing personally — it runs on your work Claude seat. No personal API keys,
no per-message charges to you.*
