"""`sudoku --verify`: a recorded game is replayed from its seed, never trusted."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sudoku_app import __version__
from sudoku_app.game import Sudoku, generate, grid_text
from sudoku_app.verify import verify_record, verify_text

SEED, DIFF = 4242, "medium"


def _record(game: Sudoku, won: bool) -> dict:
    """What the UI writes to games.jsonl (config.record_game)."""
    return {
        "won": won, "difficulty": game.difficulty, "time": int(game.elapsed),
        "mistakes": game.mistakes, "hints": game.hints, "seed": game.seed,
        "date": "2026-11-12T10:00:00", "version": __version__,
        "puzzle": grid_text(game.puzzle), "solution": grid_text(game.solution), "log": list(game.log),
    }


def _played(hints: int = 3, leave: int = 0) -> Sudoku:
    """A real game through the engine: one wrong digit fixed, a few hints,
    the rest typed in (but the last `leave` cells), a couple of seconds per move."""
    g = Sudoku(SEED, DIFF)
    empty = [i for i in range(81) if not g.puzzle[i]]
    first = empty[0]
    wrong = g.solution[first] % 9 + 1
    g.elapsed = 3
    g.toggle_note(first, wrong)
    g.elapsed = 5
    g.set_value(first, wrong)
    g.elapsed = 7
    g.undo()
    for _ in range(hints):
        g.elapsed += 4
        step = g.hint()
        cell, d = step.place
        g.set_value(cell, d, hint=True)
    for cell in empty[: len(empty) - leave]:
        if not g.values[cell]:
            g.elapsed += 2
            g.set_value(cell, g.solution[cell])
    assert g.is_solved() == (not leave)
    return g


@pytest.fixture
def won() -> dict:
    return _record(_played(), won=True)


def test_real_game_verifies(won):
    out = verify_record(won)
    assert out["ok"], out["reason"]
    assert out == {
        "ok": True, "game": "sudoku", "seed": SEED, "settings": {"difficulty": DIFF},
        "won": True, "time": won["time"], "score": None, "moves": len(won["log"]),
        "version": __version__, "reason": "", "hints": 3, "mistakes": 1,
    }


def test_game_solved_with_hints_only():
    g = Sudoku(SEED, "hard")
    while not g.is_solved():
        g.elapsed += 1
        cell, d = g.hint().place
        g.set_value(cell, d, hint=True)
    out = verify_record(_record(g, won=True))
    assert out["ok"], out["reason"]
    assert out["won"] and out["hints"] == g.hints and out["time"] == int(g.elapsed)


def test_lost_game_claiming_less_time_fails():
    rec = _record(_played(leave=2), won=False)
    rec["time"] -= 1
    assert "time" in verify_record(rec)["reason"]


def test_lost_game_is_ok_but_not_won():
    g = _played(leave=2)
    rec = _record(g, won=False)
    rec["time"] += 30  # the clock ran on after the last move before it was abandoned
    out = verify_record(rec)
    assert out["ok"] and not out["won"]
    assert out["time"] == int(g.elapsed)


def test_record_without_puzzle_still_verifies(won):
    del won["puzzle"], won["solution"]
    assert verify_record(won)["ok"]


@pytest.mark.parametrize("change, reason", [
    (lambda r: r.update(time=r["time"] - 1), "time"),
    (lambda r: r.update(time=r["time"] + 1), "time"),
    (lambda r: r.update(won=False), "solves"),
    (lambda r: r.update(hints=0), "hints"),
    (lambda r: r.update(mistakes=0), "mistakes"),
    (lambda r: r["log"].append(f"v0=1@{r['time']}"), "after the puzzle was solved"),
    (lambda r: r["log"].insert(3, "v99=1@7"), "unparsable"),
    (lambda r: r["log"].insert(3, "x1=1@7"), "unparsable"),
    (lambda r: r["log"].insert(3, "u"), "unparsable"),
    (lambda r: r.update(difficulty="hard"), "puzzle differs"),
    (lambda r: r.update(difficulty="impossible"), "unknown difficulty"),
    (lambda r: r.pop("seed"), "no seed"),
    (lambda r: r.update(seed="4242"), "no seed"),
    (lambda r: r.update(puzzle=grid_text(generate(SEED + 1, DIFF)[0])), "puzzle differs"),
    (lambda r: r.update(log=r["log"][:-1]), "claimed result"),
])
def test_tampering_fails(won, change, reason):
    change(won)
    out = verify_record(won)
    assert not out["ok"]
    assert reason in out["reason"]


def test_illegal_move_fails(won):
    given = next(i for i, v in enumerate(Sudoku(SEED, DIFF).puzzle) if v)
    won["log"].insert(3, f"v{given}=1@7")  # a given cell cannot be changed
    out = verify_record(won)
    assert not out["ok"] and "illegal move 4" in out["reason"]


def test_wrong_difficulty_is_not_the_same_puzzle(won):
    del won["puzzle"], won["solution"]
    won["difficulty"] = "easy"
    assert not verify_record(won)["ok"]


def test_too_fast_fails():
    g = _played()
    rec = _record(g, won=True)
    rec["log"] = [e.partition("@")[0] + "@1" for e in rec["log"]]
    rec["time"] = 1
    out = verify_record(rec)
    assert not out["ok"] and "too fast" in out["reason"]


def test_hint_with_a_wrong_digit_fails(won):
    g = Sudoku(SEED, DIFF)
    cell = next(i for i in range(81) if not g.puzzle[i])
    won["log"].insert(0, f"h{cell}={g.solution[cell] % 9 + 1}@0")
    assert "hint with a wrong digit" in verify_record(won)["reason"]


def test_clock_backwards_fails(won):
    won["log"][5] = won["log"][5].partition("@")[0] + "@99999"
    assert not verify_record(won)["ok"]


@pytest.mark.parametrize("text", [
    "", "not json", "[1, 2]", "null", "42", '"x"', "{}", '{"seed": 1}',
    '{"seed": 1, "difficulty": "easy", "log": "v1=1@0"}',
    '{"seed": 1, "difficulty": "easy", "log": [1, 2]}',
    '{"seed": 1e400, "difficulty": "easy"}',
    '{"seed": true, "difficulty": "easy"}',
    "[" * 100_000,
    '{"seed": 1, "difficulty": "easy", "log": [' + ",".join(['"N@1"'] * 50_000) + "]}",
])
def test_malformed_input_is_a_clean_no(text):
    out = verify_text(text)
    assert out["ok"] is False and out["reason"]
    json.dumps(out)


def _run(stdin: str, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    env.pop("SUDOKU_PROFILE", None)
    return subprocess.run(
        [sys.executable, "-m", "sudoku_app", "--verify"], input=stdin, capture_output=True,
        text=True, env=env, cwd=str(Path(__file__).resolve().parent.parent), timeout=30,
    )


def test_cli_verifies_and_writes_nothing(won, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    good = _run(json.dumps(won), home)
    assert good.returncode == 0, good.stderr
    assert good.stderr == ""
    lines = good.stdout.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["ok"] is True

    won["time"] -= 100
    bad = _run(json.dumps(won), home)
    assert bad.returncode == 1 and bad.stderr == ""
    assert json.loads(bad.stdout)["reason"] == "time differs from the replay"

    junk = _run("{not json", home)
    assert junk.returncode == 1 and junk.stderr == ""
    assert json.loads(junk.stdout)["ok"] is False

    assert list(home.iterdir()) == []
