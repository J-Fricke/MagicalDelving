#cli.py
import sys

def main() -> int:
    """
    Magical Delving — suite entrypoint.
    Usage:
      magicaldelving <tool> [args...]
    """
    argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help"):
        print(
            "Magical Delving — A Suite of cEDH Tools\n\n"
            "Usage:\n"
            "  magicaldelving <tool> [args...]\n\n"
            "Tools:\n"
            "  topdeck-meta   TopDeck Meta Diff + optional Moxfield highlight\n\n"
            "  mulligan-sim   Mulligan + draw/win turn Monte Carlo\n\n"
            "Help:\n"
            "  magicaldelving topdeck-meta -h\n"
            "  magicaldelving mulligan-sim -h\n"
        )
        return 0

    tool, rest = argv[0], argv[1:]

    if tool in ("topdeck-meta", "topdeck", "meta"):
        from magicaldelving.topdeck_meta.tool import main as tool_main
        sys.argv = [sys.argv[0]] + rest
        tool_main()
        return 0

    if tool in ("mulligan-sim", "mulligan", "sim"):
        from magicaldelving.mulligan_sim.tool import main as tool_main
        sys.argv = [sys.argv[0]] + rest
        tool_main()
        return 0

    print(f"Unknown tool: {tool}\nRun: magicaldelving -h")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
