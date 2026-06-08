import shutil
import sys
import time

from . import anthropic_client as ac
from . import claude_cli
from . import config as cfg_mod
from . import drive_client
from . import filesystem as fs
from . import filing
from . import manifest
from . import notes_intake
from . import prompts
from . import redactor
from . import router


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
            print(
                f"ERROR: no usable prompt in {cfg.prompt_library_path} — add the "
                f"routed category prompts (### DAILY./STANDUP./SOLUTION./EXEC.) "
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
        # Refresh manifest in case downstream transcript dedup needs it.
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
    total_cost = 0.0

    # claude-cli backend → run on the work seat, auto-route, and emit a shareable
    # sibling. api backend → legacy behaviour: single default prompt, no routing,
    # no shareable pass (the regression guard / fallback path).
    analyze_backend = claude_cli if cfg.backend == "claude-cli" else ac
    use_routing = cfg.backend == "claude-cli"
    redact_instruction = prompt_library.get("REDACT")

    for i, src in enumerate(candidates, 1):
        try:
            transcript = fs.read_text(src, drive_service=drive_service)

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

    total_succeeded = succeeded + notes_filed
    total_failed = failed + notes_skipped
    notes_detail = (
        f" (transcripts: {succeeded}/{succeeded + failed}; "
        f"notes: {notes_filed}/{notes_filed + notes_skipped})"
        if notes_pending
        else ""
    )
    print(
        f"All done. {total_succeeded} succeeded, {total_failed} failed. "
        f"Total cost: ${total_cost:.2f}.{notes_detail}"
    )
    return 0 if total_failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
