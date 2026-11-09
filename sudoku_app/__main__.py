"""Entry point: `sudoku` or `python -m sudoku_app`."""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sudoku",
        description="Sudoku in your terminal. Run without arguments for the menu.",
    )
    parser.add_argument("-V", "--version", action="store_true", help="show the version")
    parser.add_argument(
        "--profile", metavar="NAME",
        help="use a separate profile (own settings, stats, saved game)",
    )
    parser.add_argument(
        "--new", action="store_true", help="skip the menu and start a new puzzle right away"
    )
    parser.add_argument("--resume", action="store_true", help="reopen the saved puzzle right away")
    parser.add_argument("--no-update", action="store_true", help="skip the update check")
    parser.add_argument(
        "--difficulty", metavar="D", help="easy | medium | hard | expert",
    )
    parser.add_argument(
        "--puzzle", type=int, metavar="N",
        help="play puzzle number N (1-999999); the same number and difficulty is the same puzzle",
    )
    parser.add_argument(
        "--battle", type=int, metavar="ID",
        help="play chat battle ID (from beheer): everyone gets the same puzzle",
    )
    args = parser.parse_args()

    if args.profile:
        os.environ["SUDOKU_PROFILE"] = args.profile

    if args.version:
        from . import __version__

        print(f"sudoku v{__version__}")
        return

    # Claude-style eager update: pull before the UI starts, then re-exec so
    # you are always ON the latest version, not merely notified about it.
    if not args.no_update:
        from .update import quick_sync_update

        if quick_sync_update():
            os.execv(
                sys.executable,
                [sys.executable, "-m", "sudoku_app", *sys.argv[1:], "--no-update"],
            )

    from .game import DIFFICULTIES

    if args.difficulty and args.difficulty not in DIFFICULTIES:
        parser.error(f"unknown difficulty: {args.difficulty} (choose from {', '.join(DIFFICULTIES)})")
    if args.puzzle is not None and not 1 <= args.puzzle <= 999_999:
        parser.error("puzzle numbers run from 1 to 999999")

    initial = None
    if args.battle is not None:
        from .game import battle_seed

        initial = {"difficulty": args.difficulty or "easy", "seed": battle_seed(args.battle),
                   "battle": args.battle}
    elif args.new or args.difficulty or args.puzzle is not None:
        from .config import load_settings

        initial = {
            "difficulty": args.difficulty or str(load_settings().get("difficulty", "easy")),
            "seed": args.puzzle,
        }

    from .ui.app import run

    run(no_update=args.no_update, initial=initial, resume=args.resume)


if __name__ == "__main__":
    main()
