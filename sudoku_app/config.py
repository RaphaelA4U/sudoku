"""Settings, statistics, the saved game and the game log in ~/.config/sudoku/.

Set SUDOKU_PROFILE (or run `sudoku --profile name`) to use a separate
profile directory with its own settings, stats, and saved game.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "sudoku"
_profile = os.environ.get("SUDOKU_PROFILE", "").strip()
if _profile:
    CONFIG_DIR = CONFIG_DIR / "profiles" / _profile
SETTINGS_FILE = CONFIG_DIR / "settings.json"
STATS_FILE = CONFIG_DIR / "stats.json"
SAVE_FILE = CONFIG_DIR / "saved_game.json"
GAMES_FILE = CONFIG_DIR / "games.jsonl"  # every finished puzzle with all its moves

MISTAKE_MODES = ["wrong", "conflicts", "off"]
MISTAKE_LABELS = {"wrong": "show wrong digits", "conflicts": "show conflicts", "off": "hidden"}
MISTAKE_INFO = {
    "wrong": "A digit that does not match the solution turns red right away.",
    "conflicts": "Only a digit that clashes with its row, column or box turns red.",
    "off": "Nothing turns red: find your own mistakes.",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "difficulty": "medium",     # easy | medium | hard | expert
    "mistakes": "off",          # wrong | conflicts | off
    "win_animation": True,      # fireworks when you solve it
    "timer": True,              # show the clock
    "last_seen_version": None,  # for the what's-new note after updates
}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(path)
    except OSError:
        pass


def load_settings() -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    settings.update(_read_json(SETTINGS_FILE, {}))
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    _write_json(SETTINGS_FILE, settings)


def save_game_snapshot(snapshot: dict[str, Any]) -> None:
    _write_json(SAVE_FILE, snapshot)


def load_game_snapshot() -> dict[str, Any] | None:
    snapshot = _read_json(SAVE_FILE, None)
    return snapshot if isinstance(snapshot, dict) else None


def clear_saved_game() -> None:
    try:
        SAVE_FILE.unlink()
    except OSError:
        pass


# -- statistics and the game log ---------------------------------------------


def load_stats() -> dict[str, Any]:
    stats = _read_json(STATS_FILE, {})
    if not isinstance(stats, dict):
        stats = {}
    stats.setdefault("games", [])
    return stats


def record_game(entry: dict[str, Any], game: dict[str, Any] | None = None) -> None:
    """Record one solved or abandoned puzzle (abandoning = not solved). With
    the game itself, the whole game (puzzle, solution, every action) is also
    appended to games.jsonl, one line per puzzle (a few KB), so it can be
    replayed and reviewed later."""
    date = datetime.now().isoformat(timespec="seconds")
    stats = load_stats()
    stats["games"].append(dict(entry, date=date))
    _write_json(STATS_FILE, stats)
    if game is not None:
        line = dict(entry, date=date, puzzle=game.get("puzzle"), solution=game.get("solution"),
                    log=list(game.get("log", [])))
        try:
            GAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with GAMES_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass


def load_game_logs() -> list[dict[str, Any]]:
    """Every recorded puzzle with its moves, oldest first."""
    games = []
    try:
        lines = GAMES_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return games
    for line in lines:
        try:
            games.append(json.loads(line))
        except ValueError:
            continue
    return games


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _streaks(games: list[dict]) -> tuple[int, int]:
    best = cur = 0
    for g in games:
        if g.get("won"):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return cur, best


def _block(title: str, games: list[dict]) -> list[str]:
    n = len(games)
    won = [g for g in games if g.get("won")]
    cur, best = _streaks(games)
    lines = [
        f"[b]{title}[/b]",
        f"  Played {n:>4}   solved {len(won):>4} ({100 * len(won) / n:.0f}%)   "
        f"streak {cur} (best {best})",
    ]
    if won:
        times = [g.get("time", 0) for g in won]
        clean = [g for g in won if not g.get("mistakes") and not g.get("hints")]
        lines.append(
            f"  Fastest {_fmt_time(min(times))}   average {_fmt_time(sum(times) / len(times))}   "
            f"without mistakes or hints {len(clean)}"
        )
    return lines


def stats_summary() -> list[str]:
    from .game import DIFFICULTIES

    games = load_stats()["games"]
    if not games:
        return [
            "No puzzles recorded yet. A puzzle counts once you have filled in a",
            "digit: solved, or not solved when you start another one instead.",
        ]
    lines = _block("Overall", games)
    for diff in DIFFICULTIES:
        subset = [g for g in games if g.get("difficulty") == diff]
        if subset:
            lines += [""] + _block(diff.capitalize(), subset)
    return lines
