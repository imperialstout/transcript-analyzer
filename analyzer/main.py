import sys
import time

from . import anthropic_client as ac
from . import config as cfg_mod
from . import drive_client
from . import filesystem as fs
from . import filing
from . import manifest
from . import notes_intake
from . import prompts


def main() -> int:
    cfg = cfg_mod.CONFIG

    if not cfg.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env or environment", file=sys.stderr)
        return 1

    try:
        prompt_library = prompts.load_prompts()
    except FileNotFoundError as e:
        print(f"ERROR: prompt library not found — {e}", file=sys.stderr)
        return 1

    if cfg.default_prompt_key not in prompt_library:
        print(
            f"ERROR: prompt key {cfg.default_prompt_key!r} not found in {cfg.prompt_library_path}",
            file=sys.stderr,
        )
        return 1

    try:
        context_brief = prompts.load_context_brief()
    except FileNotFoundError as e:
        print(f"ERROR: context brief not found — {e}", file=sys.stderr)
        return 1

    frontmatter_instr = prompts.frontmatter_instruction()

    existing_manifest = manifest.load()

    # Notes intake first — fast, no LLM, fails closed (file stays in place).
    notes = notes_intake.list_pending_notes()
    notes_pending = [n for n in notes if not manifest.is_recorded(n.name, existing_manifest)]
    notes_filed = 0
    notes_skipped = 0
    if notes_pending:
        # Only initialize Drive (and pay the OAuth-token check cost) if a
        # .gdoc is actually pending. Failure is non-fatal: log and skip
        # those, .txt notes still process.
        drive_service = None
        gdocs_pending = [n for n in notes_pending if n.suffix == ".gdoc"]
        if gdocs_pending:
            try:
                drive_service = drive_client.get_drive_service()
            except Exception as e:
                print(
                    f"Drive service unavailable — {len(gdocs_pending)} .gdoc note(s) "
                    f"will be skipped: {e}",
                    file=sys.stderr,
                )

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
        if fs.fuzzy_is_analyzed(txt):
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

    for i, src in enumerate(candidates, 1):
        prompt_key = cfg.default_prompt_key
        model = cfg_mod.model_for(prompt_key)
        print(f"  [{i}/{n}] {src.name} → {prompt_key} ({model})")

        try:
            transcript = fs.read_text(src)

            t0 = time.monotonic()
            result = ac.analyze(
                transcript_text=transcript,
                prompt_body=prompt_library[prompt_key],
                context_brief=context_brief,
                frontmatter_instruction=frontmatter_instr,
                model=model,
            )
            duration = time.monotonic() - t0

            output_filename, meeting_date = filing.build_output_filename(src.name)
            output_path = cfg.analyzed_path / output_filename
            fs.write_text(output_path, result.text)

            entry = manifest.record(
                src.name,
                output_filename=output_filename,
                prompt_key=prompt_key,
                model=model,
                usage=result.usage,
                duration_seconds=duration,
            )
            cost = entry["cost_usd"]
            total_cost += cost

            fs.move_to_processed(src, meeting_date)
            print(
                f"  [{i}/{n}] done — filed as {output_filename} "
                f"(${cost:.2f}, {duration:.1f}s, "
                f"cache_read={result.usage.cache_read_input_tokens})"
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
