"""`sudoku --verify`: replay one recorded game from scratch and check it.

The record is one line of games.jsonl. The puzzle is rebuilt from the seed and
the difficulty with the game's own generator (a puzzle or solution stored in
the record is never trusted), every action in the log is applied with the
engine's own rules, and the result is computed, not copied.

This module never writes a file, never touches the network and never imports
the UI, so a server can run it in a sandbox or call `verify_record` directly.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from . import __version__
from .game import DIFFICULTIES, Sudoku, grid_text

MAX_INPUT = 2_000_000   # bytes of JSON on stdin; a real record is a few KB
MAX_LOG = 20_000        # log entries; a real game has a few hundred
MIN_SECONDS_PER_DIGIT = 0.3
SEED_MAX = 999_999

_SET = re.compile(r"([vh])(\d{1,2})=([1-9])(!?)")
_NOTE = re.compile(r"n(\d{1,2})=([1-9])")
_CLEAR = re.compile(r"c(\d{1,2})")
_CLOCK = re.compile(r"\d{1,9}")


class _Bad(Exception):
    """The record does not hold up; the message is the reason."""


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _cell(text: str) -> int:
    cell = int(text)
    if not 0 <= cell <= 80:
        raise ValueError
    return cell


def _apply(game: Sudoku, action: str) -> bool:
    """Apply one logged action; False when the engine refuses it (the game
    only logs actions that changed something, so a refusal is illegal)."""
    if action == "u":
        return game.undo()
    if action == "N":
        return game.fill_notes()
    if action == "R":
        return game.restart()
    m = _CLEAR.fullmatch(action)
    if m:
        return game.clear(_cell(m.group(1)))
    m = _NOTE.fullmatch(action)
    if m:
        return game.toggle_note(_cell(m.group(1)), int(m.group(2)))
    m = _SET.fullmatch(action)
    if m:
        kind, cell, d, keep = m.group(1), _cell(m.group(2)), int(m.group(3)), m.group(4)
        if kind == "h" and d != game.solution[cell]:
            raise _Bad(f"hint with a wrong digit: {action}")
        return game.set_value(cell, d, hint=kind == "h", clean_notes=not keep)
    raise ValueError


def verify_record(record: Any) -> dict[str, Any]:
    """Replay `record` (one games.jsonl line, parsed) and return the verdict:
    ok, game, seed, settings, won, time, score, moves, version, reason, hints, mistakes."""
    out: dict[str, Any] = {
        "ok": False, "game": "sudoku", "seed": None, "settings": {"difficulty": None},
        "won": False, "time": None, "score": None, "moves": 0, "version": __version__,
        "reason": "", "hints": None, "mistakes": None,
    }
    try:
        out.update(_verify(record, out))
        out["ok"] = True
    except _Bad as exc:
        out["reason"] = str(exc)
    except Exception:  # never a traceback: anything unexpected is a bad record
        out["reason"] = "record could not be replayed"
    return out


def _verify(record: Any, out: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise _Bad("record is not a JSON object")
    if record.get("game", "sudoku") != "sudoku":
        raise _Bad("record is not a sudoku game")

    seed = _int(record.get("seed"))
    if seed is None:
        raise _Bad("no seed")
    if not 1 <= seed <= SEED_MAX:
        raise _Bad("seed out of range")
    out["seed"] = seed

    difficulty = record.get("difficulty")
    if difficulty is None and isinstance(record.get("settings"), dict):
        difficulty = record["settings"].get("difficulty")
    if isinstance(difficulty, str) and len(difficulty) <= 20:
        out["settings"] = {"difficulty": difficulty}
    if difficulty not in DIFFICULTIES:
        raise _Bad("unknown difficulty")

    log = record.get("log")
    if log is None:
        log = []
    if not isinstance(log, list):
        raise _Bad("log is not a list")
    if len(log) > MAX_LOG:
        raise _Bad("log is too long")
    out["moves"] = len(log)

    game = Sudoku(seed, difficulty)
    for key, made in (("puzzle", game.puzzle), ("solution", game.solution)):
        stored = record.get(key)
        if stored is not None and stored != grid_text(made):
            raise _Bad("puzzle differs (made by another version)")

    clock = 0
    placed = 0
    solved_at: Optional[int] = None
    for i, entry in enumerate(log):
        if not isinstance(entry, str) or len(entry) > 24:
            raise _Bad(f"unparsable move {i + 1}")
        action, at, stamp = entry.partition("@")
        if not at or not _CLOCK.fullmatch(stamp):
            raise _Bad(f"unparsable move {i + 1}: {entry}")
        now = int(stamp)
        if now < clock:
            raise _Bad(f"clock runs backwards at move {i + 1}")
        clock = now
        if solved_at is not None:
            raise _Bad("moves after the puzzle was solved")
        game.elapsed = float(now)
        try:
            done = _apply(game, action)
        except _Bad:
            raise
        except (ValueError, IndexError):
            raise _Bad(f"unparsable move {i + 1}: {entry}") from None
        if not done:
            raise _Bad(f"illegal move {i + 1}: {entry}")
        if action[0] in "vh":
            placed += 1
        if game.is_solved():
            solved_at = i

    won = solved_at is not None
    result = {"won": won, "time": clock, "hints": game.hints, "mistakes": game.mistakes}
    out.update(result)

    claimed_won = record.get("won")
    if claimed_won is not None and claimed_won is not won:
        raise _Bad("the log does not end in the claimed result"
                   if claimed_won else "the log solves the puzzle but the record says not solved")
    claimed_time = record.get("time")
    if claimed_time is not None:
        claimed = _int(claimed_time)
        # A finished puzzle stops the clock on its last move. An abandoned one
        # keeps running after the last move, so it may claim more, never less.
        if claimed is None or claimed < clock or (won and claimed != clock):
            raise _Bad("time differs from the replay")
    for key in ("hints", "mistakes"):
        if key in record and _int(record[key]) != result[key]:
            raise _Bad(f"{key} differ from the replay")
    # Only a solved puzzle can rank, so only that one has to be humanly possible:
    # an abandoned game often has a digit placed in its first second.
    if won and clock < MIN_SECONDS_PER_DIGIT * placed:
        raise _Bad("too fast: less than 0.3 s per placed digit")
    return result


def verify_text(text: str) -> dict[str, Any]:
    """Verify a record given as JSON text."""
    try:
        record = json.loads(text)
    except Exception:
        return verify_record_error("not valid JSON")
    return verify_record(record)


def verify_record_error(reason: str) -> dict[str, Any]:
    out = verify_record(None)
    out["reason"] = reason
    return out


def main(stdin: Any) -> int:
    """`sudoku --verify`: read one record from stdin, print one JSON line,
    return the exit code (0 ok, 1 not)."""
    try:
        raw = stdin.buffer.read(MAX_INPUT + 1) if hasattr(stdin, "buffer") else stdin.read(MAX_INPUT + 1)
        if isinstance(raw, bytes):
            if len(raw) > MAX_INPUT:
                result = verify_record_error("record is too large")
            else:
                result = verify_text(raw.decode("utf-8"))
        elif len(raw) > MAX_INPUT:
            result = verify_record_error("record is too large")
        else:
            result = verify_text(raw)
    except Exception:
        result = verify_record_error("could not read the record")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["ok"] else 1
