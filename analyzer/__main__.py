import sys


def main() -> int:
    # Dispatch:
    #   `python -m analyzer synthesize ...` → synthesize.main()
    #   `python -m analyzer ui [--port N]`  → ui.main()
    #   `python -m analyzer`                → main.main()
    if len(sys.argv) > 1 and sys.argv[1] == "synthesize":
        sys.argv = [sys.argv[0]] + sys.argv[2:]  # strip "synthesize" so argparse sees --mode
        from .synthesize import main as synth_main
        return synth_main()

    if len(sys.argv) > 1 and sys.argv[1] == "ui":
        sys.argv = [sys.argv[0]] + sys.argv[2:]  # strip "ui" so argparse sees --port
        from .ui import main as ui_main
        return ui_main()

    force = "--force" in sys.argv
    if force:
        sys.argv = [a for a in sys.argv if a != "--force"]
    from .main import main as analyze_main
    return analyze_main(force=force)


sys.exit(main())
