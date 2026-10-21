import sys


def _item_cmd(argv: list[str]) -> int:
    """Dispatch: python -m analyzer item <subcommand> <id> [--note "..."]

    Subcommands:
      close <id> [--note "..."]  — mark complete
      drop  <id> [--note "..."]  — mark dropped
    """
    if len(argv) < 2:
        print("Usage: python -m analyzer item close|drop <id> [--note \"...\"]", file=sys.stderr)
        return 1

    subcmd = argv[0]
    if subcmd not in ("close", "drop"):
        print(f"Unknown item subcommand: {subcmd!r}. Use 'close' or 'drop'.", file=sys.stderr)
        return 1

    status = "complete" if subcmd == "close" else "dropped"

    # Parse id and optional --note
    note = ""
    remaining = argv[1:]
    item_id_prefix = None
    i = 0
    while i < len(remaining):
        if remaining[i] == "--note" and i + 1 < len(remaining):
            note = remaining[i + 1]
            i += 2
        elif not remaining[i].startswith("--"):
            item_id_prefix = remaining[i]
            i += 1
        else:
            i += 1

    if not item_id_prefix:
        print("Error: item id required.", file=sys.stderr)
        return 1

    from . import brief_state

    items = brief_state._load()
    # Resolve by exact match first, then unambiguous prefix
    matches = [it for it in items if it["id"] == item_id_prefix]
    if not matches:
        matches = [it for it in items if it["id"].startswith(item_id_prefix)]
    if len(matches) == 0:
        print(f"Error: no item found matching id prefix {item_id_prefix!r}.", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"Error: ambiguous prefix {item_id_prefix!r} matches {len(matches)} items:", file=sys.stderr)
        for m in matches:
            print(f"  {m['id']}  {m['text'][:80]}", file=sys.stderr)
        return 1

    target = matches[0]
    success = brief_state.close(target["id"], status, notes=note)
    if success:
        print(f"{subcmd.capitalize()}d: {target['id']} — {target['text'][:80]}")
    else:
        print(f"Error: failed to update item {target['id']}.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    # Dispatch:
    #   `python -m analyzer synthesize ...`      → synthesize.main()
    #   `python -m analyzer ui [--port N]`       → ui.main()
    #   `python -m analyzer item close|drop ...` → _item_cmd()
    #   `python -m analyzer`                     → main.main()
    if len(sys.argv) > 1 and sys.argv[1] == "synthesize":
        sys.argv = [sys.argv[0]] + sys.argv[2:]  # strip "synthesize" so argparse sees --mode
        from .synthesize import main as synth_main
        return synth_main()

    if len(sys.argv) > 1 and sys.argv[1] == "ui":
        sys.argv = [sys.argv[0]] + sys.argv[2:]  # strip "ui" so argparse sees --port
        from .ui import main as ui_main
        return ui_main()

    if len(sys.argv) > 1 and sys.argv[1] == "item":
        return _item_cmd(sys.argv[2:])

    force = "--force" in sys.argv
    if force:
        sys.argv = [a for a in sys.argv if a != "--force"]
    from .main import main as analyze_main
    return analyze_main(force=force)


sys.exit(main())
