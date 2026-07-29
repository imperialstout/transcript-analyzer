import fcntl
import shutil
import sys
import time
from pathlib import Path

from . import anthropic_client as ac
from . import claude_cli
from . import config as cfg_mod
from . import drive_client
from . import filesystem as fs
from . import filing
from . import manifest
from . import notes_intake
from . import plaud_intake
from . import prompts
from . import redactor
from . import router

# Floor for a transcript body to be considered real content. A freshly
# (re)downloaded Drive file can read back as "" or a tiny stub before its body
# syncs; anything under this is treated as a failed read (fail closed) rather
# than fed to the model. Real transcripts are KBs — even a one-line note clears
# this comfortably.
_MIN_TRANSCRIPT_CHARS = 50

_REFERENCE_UPDATE_SYSTEM = """\
You are maintaining a program reference document. You will receive:
  1. The current contents of [PROGRAM REFERENCE].md (may be empty on first run).
  2. A "Reference Updates" section extracted from a newly analyzed document.

Your task: produce an updated [PROGRAM REFERENCE].md that incorporates the new
facts. Rules:
- Preserve existing content unless a new fact supersedes or corrects it.
- Only include durable facts — things that will still be true in 30 days.
- Do NOT include status updates, action items, or meeting-specific observations.
- Write in clean markdown with logical section headings (e.g. ## Team Structure,
  ## Milestones, ## Process & Ways of Working, ## Stakeholders). Add or create
  sections as needed.
- Be concise. One bullet per fact. No padding.
- Output ONLY the updated markdown document — no preamble, no explanation.
"""


def _update_program_reference(reference_updates_section: str) -> None:
    """Rewrite [PROGRAM REFERENCE].md by merging in new durable facts.

    Best-effort: if this fails, only the reference file is stale — the document
    analysis itself is already recorded and the source already moved.
    """
    current = fs.read_program_reference()
    payload = (
        f"=== CURRENT [PROGRAM REFERENCE].md ===\n\n{current}\n\n"
        f"=== REFERENCE UPDATES FROM NEW DOCUMENT ===\n\n{reference_updates_section}"
    )
    data = claude_cli.run_claude_p(
        payload,
        model=cfg_mod.model_for("DOCUMENT"),
        system=_REFERENCE_UPDATE_SYSTEM,
    )
    updated = claude_cli.result_text(data)
    if updated.strip():
        fs.write_program_reference(updated)


def _extract_reference_updates(analysis_text: str) -> str:
    """Pull the '## Reference Updates' section out of a document analysis.

    Returns the section body (without the heading), or "" if absent.
    """
    import re
    m = re.search(
        r"^##\s+Reference Updates\s*\n(.*?)(?=^##|\Z)",
        analysis_text,
        re.MULTILINE | re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def _resolve_prompt(prompt_library: dict, prompt_key: str, cfg) -> tuple[str, str]:
    """Return (prompt_body, resolved_key), falling back if `prompt_key` is absent.

    A routed category may not be in PromptLibrary.md yet (Drive edit lags the
    code). Rather than fail the transcript, fall back to the FALLBACK category,
    then the legacy default prompt. Raises only if nothing usable exists (caught
    per-transcript → fails closed)."""
    if prompt_key in prompt_library:
        return prompt_library[prompt_key], prompt_key
    for fb in (router.FALLBACK, cfg.default_prompt_key):
        if fb in prompt_library:
            print(
                f"  [router] prompt {prompt_key!r} not in library; using {fb!r}",
                file=sys.stderr,
            )
            return prompt_library[fb], fb
    raise KeyError(
        f"no prompt body for {prompt_key!r} and no fallback "
        f"({router.FALLBACK!r}/{cfg.default_prompt_key!r}) in library"
    )


def main(force: bool = False) -> int:
    cfg = cfg_mod.CONFIG

    # Prevent concurrent runs (UI-triggered vs launchd) from racing on the
    # same inbox files. flock is non-blocking: a second invocation prints a
    # message and exits 0 so launchd doesn't count it as a failure.
    lock_path = Path("/tmp/transcript-analyzer.lock")
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another run is already in progress — skipping this invocation.")
        lock_fh.close()
        return 0

    try:
        return _main_locked(cfg, force)
    finally:
        lock_fh.close()


def _main_locked(cfg, force: bool) -> int:
    # Credential gate is backend-specific. The claude-cli backend uses the work
    # Claude Code seat (no personal key) — so it must NOT require ANTHROPIC_API_KEY;
    # it requires the `claude` binary instead. This is what makes "zero personal-key
    # usage" structural rather than incidental.
    if cfg.backend == "api":
        if not cfg.anthropic_api_key:
            print("ERROR: ANTHROPIC_API_KEY not set in .env or environment", file=sys.stderr)
            return 1
    elif cfg.backend == "claude-cli":
        if shutil.which(cfg.claude_bin) is None:
            print(
                f"ERROR: claude CLI not found ({cfg.claude_bin!r}). Install the "
                f"Claude Code seat or set CLAUDE_BIN to an absolute path.",
                file=sys.stderr,
            )
            return 1
    else:
        print(
            f"ERROR: unknown BACKEND {cfg.backend!r} (expected 'claude-cli' or 'api')",
            file=sys.stderr,
        )
        return 1

    try:
        prompt_library = prompts.load_prompts()
    except FileNotFoundError as e:
        print(f"ERROR: prompt library not found — {e}", file=sys.stderr)
        return 1

    if cfg.backend == "api":
        # Legacy single-prompt path needs exactly the configured default key.
        if cfg.default_prompt_key not in prompt_library:
            print(
                f"ERROR: prompt key {cfg.default_prompt_key!r} not found in {cfg.prompt_library_path}",
                file=sys.stderr,
            )
            return 1
    else:
        # Routing path: need at least one usable prompt (fallback category or the
        # legacy default) to analyze anything; warn on any missing categories.
        if (
            router.FALLBACK not in prompt_library
            and cfg.default_prompt_key not in prompt_library
        ):
            wanted = "/".join(f"### {c}." for c in router.CATEGORIES)
            print(
                f"ERROR: no usable prompt in {cfg.prompt_library_path} — add the "
                f"routed category prompts ({wanted}) "
                f"or a {cfg.default_prompt_key!r} fallback.",
                file=sys.stderr,
            )
            return 1
        missing = [c for c in router.CATEGORIES if c not in prompt_library]
        if missing:
            print(
                f"WARNING: missing category prompt(s) {', '.join(missing)} in "
                f"{cfg.prompt_library_path}; transcripts routed there fall back.",
                file=sys.stderr,
            )

    try:
        context_brief = prompts.load_context_brief()
    except FileNotFoundError as e:
        print(f"ERROR: context brief not found — {e}", file=sys.stderr)
        return 1

    # Rolodex + term glossary are optional — best-effort, "" if absent (neither
    # loader raises). The glossary normalizes mangled names/terms in non-Plaud
    # transcripts (Gemini/Teams/Slack), which never got Plaud's device-level fix.
    rolodex = prompts.load_rolodex()
    if not rolodex:
        print(f"No people rolodex at {cfg.rolodex_path} — proceeding without it.")
    vocabulary = prompts.load_vocabulary()
    if not vocabulary:
        print(f"No term glossary at {cfg.vocabulary_path} — proceeding without it.")

    frontmatter_instr = prompts.frontmatter_instruction()

    existing_manifest = manifest.load()

    # Notes intake first — fast, no LLM, fails closed (file stays in place).
    # Drive service is used by two pipelines:
    #   - notes intake (.gdoc body export)
    #   - transcript reads, as the last-resort EDEADLK fallback in fs.read_text
    # Initialize once, eagerly, so both pipelines share the same handle.
    # Failure is non-fatal: notes pipeline skips .gdocs, transcript pipeline
    # loses its Drive fallback but local reads still work for healthy files.
    drive_service = None
    try:
        drive_service = drive_client.get_drive_service()
    except Exception as e:
        print(f"Drive service unavailable — {e}", file=sys.stderr)

    # Plaud sync — download new recordings into the inbox before the transcript
    # pipeline runs so they're processed in the same invocation.
    if cfg.plaud_enabled:
        plaud_intake.sync(days=cfg.plaud_days, plaud_bin=cfg.plaud_bin)

    notes = notes_intake.list_pending_notes()
    notes_pending = [n for n in notes if not manifest.is_recorded(n.name, existing_manifest)]
    notes_filed = 0
    notes_skipped = 0
    if notes_pending:
        print(f"Found {len(notes_pending)} pending note(s) in {cfg.notes_path}.")
        for note in notes_pending:
            entry = notes_intake.process_note(note, drive_service=drive_service)
            if entry:
                notes_filed += 1
            else:
                notes_skipped += 1
        existing_manifest = manifest.load()

    # Document pipeline — PDFs/decks dropped in Call Transcripts/docs/.
    # Analyzed with the DOCUMENT prompt, then a reference update pass rewrites
    # [PROGRAM REFERENCE].md with the durable facts extracted from each doc.
    doc_prompt_body = prompt_library.get("DOCUMENT") or prompts.default_document_prompt()
    doc_model = cfg_mod.model_for("DOCUMENT")
    pending_docs = fs.list_pending_documents()
    pending_docs = [d for d in pending_docs if not manifest.is_recorded(d.name, existing_manifest)]
    docs_succeeded = 0
    docs_failed = 0
    total_cost = 0.0

    if pending_docs:
        print(f"Found {len(pending_docs)} pending document(s) in {cfg.docs_path}.")
        for i, doc in enumerate(pending_docs, 1):
            try:
                doc_text = fs.read_document(doc)

                system = ac.system_prompt_text(
                    context_brief,
                    doc_prompt_body,
                    prompts.frontmatter_instruction(),
                    rolodex,
                    vocabulary,
                )
                t0 = time.monotonic()
                data = claude_cli.run_claude_p(
                    f"Document to analyze:\n\n{doc_text}",
                    model=doc_model,
                    system=system,
                )
                duration = time.monotonic() - t0
                analysis_text = claude_cli.result_text(data)
                if not analysis_text.strip():
                    raise RuntimeError("claude -p returned empty result for document")

                output_filename, doc_date = filing.build_output_filename(
                    doc.name, extension=".md"
                )
                output_path = cfg.analyzed_path / output_filename
                fs.write_text(output_path, analysis_text)
                print(
                    f"  [doc {i}/{len(pending_docs)}] {doc.name} → {output_filename} "
                    f"({duration:.1f}s)"
                )

                # Reference update pass — extract durable facts and rewrite
                # [PROGRAM REFERENCE].md. Best-effort: failure here does NOT
                # prevent recording the document analysis.
                ref_updates = _extract_reference_updates(analysis_text)
                if ref_updates and ref_updates.lower() != "none.":
                    try:
                        _update_program_reference(ref_updates)
                        print(f"  [doc {i}/{len(pending_docs)}] program reference updated")
                    except Exception as e:
                        print(
                            f"  [doc {i}/{len(pending_docs)}] reference update failed "
                            f"({type(e).__name__}: {e}); analysis still recorded",
                            file=sys.stderr,
                        )
                else:
                    print(f"  [doc {i}/{len(pending_docs)}] no reference updates extracted")

                # Shareable pass — same best-effort pattern as transcripts.
                shareable_name = None
                if cfg.shareable_enabled:
                    try:
                        shareable_text = redactor.redact(
                            analysis_text,
                            model=cfg.redaction_model,
                            instruction=prompt_library.get("REDACT"),
                        )
                        shareable_name = filing.shareable_filename(output_filename)
                        fs.write_text(cfg.analyzed_path / shareable_name, shareable_text)
                    except Exception as e:
                        print(
                            f"  [doc {i}/{len(pending_docs)}] shareable pass failed "
                            f"({type(e).__name__}: {e}); kept internal analysis only",
                            file=sys.stderr,
                        )

                manifest.record(
                    doc.name,
                    output_filename=output_filename,
                    prompt_key="DOCUMENT",
                    model=doc_model,
                    usage=claude_cli._usage_from_json(data),
                    duration_seconds=duration,
                    category="DOCUMENT",
                    shareable_filename=shareable_name,
                )

                # Move processed doc to docs/_Processed/<YYYY-MM>/
                target_dir = cfg.docs_processed_path / f"{doc_date.year:04d}-{doc_date.month:02d}"
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / doc.name
                if not target.exists():
                    doc.rename(target)
                else:
                    print(
                        f"  [doc {i}/{len(pending_docs)}] WARNING: {doc.name} already in "
                        f"_Processed — source left in place",
                        file=sys.stderr,
                    )

                docs_succeeded += 1
                total_cost += manifest.load().get(doc.name, {}).get("cost_usd", 0.0)

            except Exception as e:
                print(
                    f"  [doc {i}/{len(pending_docs)}] FAILED: {type(e).__name__}: {e}",
                    file=sys.stderr,
                )
                docs_failed += 1

        existing_manifest = manifest.load()

    try:
        txts, gdoc_count = fs.list_unanalyzed_transcripts()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    candidates: list = []
    fuzzy_skipped: list[str] = []
    for txt in txts:
        if manifest.is_recorded(txt.name, existing_manifest):
            continue
        if not force and fs.fuzzy_is_analyzed(txt):
            fuzzy_skipped.append(txt.name)
            continue
        candidates.append(txt)

    if gdoc_count:
        print(f"Skipped {gdoc_count} .gdoc file(s) (waiting on Zap update to .txt).")
    for name in fuzzy_skipped:
        print(f"  (skipping {name} — fuzzy-matched against an existing Analyzed/ file)")

    n = len(candidates)
    print(f"Found {n} unanalyzed transcript{'s' if n != 1 else ''}.")

    succeeded = 0
    failed = 0

    # claude-cli backend → run on the work seat, auto-route, and emit a shareable
    # sibling. api backend → legacy behaviour: single default prompt, no routing,
    # no shareable pass (the regression guard / fallback path).
    analyze_backend = claude_cli if cfg.backend == "claude-cli" else ac
    use_routing = cfg.backend == "claude-cli"
    redact_instruction = prompt_library.get("REDACT")

    for i, src in enumerate(candidates, 1):
        try:
            transcript = fs.read_text(src, drive_service=drive_service)

            # Fail closed on an empty/stub read. Drive's File Provider can serve a
            # 0-byte placeholder for a freshly (re)downloaded file before the body
            # finishes syncing — read_text succeeds with "" and, left unguarded,
            # the model replies "no transcript provided", which passes the
            # empty-OUTPUT guard and gets recorded as success, poisoning dedup.
            # Raising here keeps the source in place to retry next run.
            if len(transcript.strip()) < _MIN_TRANSCRIPT_CHARS:
                raise ValueError(
                    f"transcript body is {len(transcript.strip())} chars after read "
                    f"(< {_MIN_TRANSCRIPT_CHARS} floor) — likely an unsynced Drive "
                    f"placeholder; leaving in place to retry next run"
                )

            if use_routing:
                prompt_key = router.classify(transcript, model=cfg.classifier_model)
            else:
                prompt_key = cfg.default_prompt_key
            prompt_body, prompt_key = _resolve_prompt(prompt_library, prompt_key, cfg)
            model = cfg_mod.model_for(prompt_key)
            category = prompt_key if use_routing else None
            print(f"  [{i}/{n}] {src.name} → {prompt_key} ({model})")

            t0 = time.monotonic()
            result = analyze_backend.analyze(
                transcript_text=transcript,
                prompt_body=prompt_body,
                context_brief=context_brief,
                frontmatter_instruction=frontmatter_instr,
                model=model,
                rolodex=rolodex,
                vocabulary=vocabulary,
            )
            duration = time.monotonic() - t0

            output_filename, meeting_date = filing.build_output_filename(src.name)
            output_path = cfg.analyzed_path / output_filename
            fs.write_text(output_path, result.text)

            # Shareable redaction pass — BEST-EFFORT by design. The internal
            # analysis is the primary artifact and already paid for, so a
            # redaction failure drops only the shareable file; it does not fail
            # the record or re-queue the (expensive) internal analysis.
            shareable_name = None
            if cfg.shareable_enabled:
                try:
                    shareable_text = redactor.redact(
                        result.text,
                        model=cfg.redaction_model,
                        instruction=redact_instruction,
                    )
                    shareable_name = filing.shareable_filename(output_filename)
                    fs.write_text(cfg.analyzed_path / shareable_name, shareable_text)
                except Exception as e:
                    print(
                        f"  [{i}/{n}] shareable pass failed "
                        f"({type(e).__name__}: {e}); kept internal analysis only",
                        file=sys.stderr,
                    )
                    shareable_name = None

            entry = manifest.record(
                src.name,
                output_filename=output_filename,
                prompt_key=prompt_key,
                model=model,
                usage=result.usage,
                duration_seconds=duration,
                category=category,
                shareable_filename=shareable_name,
            )
            cost = entry["cost_usd"]
            total_cost += cost

            fs.move_to_processed(src, meeting_date)
            shareable_note = ", +shareable" if shareable_name else ""
            print(
                f"  [{i}/{n}] done — filed as {output_filename} "
                f"(${cost:.2f}, {duration:.1f}s, "
                f"cache_read={result.usage.cache_read_input_tokens}{shareable_note})"
            )
            succeeded += 1
        except Exception as e:
            print(f"  [{i}/{n}] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            failed += 1

    total_succeeded = succeeded + notes_filed + docs_succeeded
    total_failed = failed + notes_skipped + docs_failed
    detail_parts = []
    detail_parts.append(f"transcripts: {succeeded}/{succeeded + failed}")
    if notes_pending:
        detail_parts.append(f"notes: {notes_filed}/{notes_filed + notes_skipped}")
    if pending_docs:
        detail_parts.append(f"docs: {docs_succeeded}/{docs_succeeded + docs_failed}")
    detail = f" ({'; '.join(detail_parts)})" if len(detail_parts) > 1 else ""
    print(
        f"All done. {total_succeeded} succeeded, {total_failed} failed. "
        f"Total cost: ${total_cost:.2f}.{detail}"
    )
    return 0 if total_failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
