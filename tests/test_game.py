"""The rules engine, the generator and the logical solver."""

import random

import pytest

from sudoku_app.game import (
    DIFFICULTIES,
    LEVELS,
    PEERS,
    Logic,
    Sudoku,
    count_solutions,
    generate,
    grid_text,
    rate,
    solve,
)


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_generated_puzzles_are_unique_and_rated(difficulty):
    for seed in (1, 2, 3):
        puzzle, solution = generate(seed, difficulty)
        assert count_solutions(puzzle) == 1
        assert solve(puzzle) == solution
        assert all(p in (0, s) for p, s in zip(puzzle, solution))
        lo, hi = LEVELS[difficulty]
        assert lo <= rate(puzzle) <= hi
    # Same number, same puzzle; another difficulty, another puzzle.
    assert generate(5, difficulty) == generate(5, difficulty)


def test_difficulties_get_harder():
    clues = {d: sum(1 for v in generate(9, d)[0] if v) for d in DIFFICULTIES}
    assert clues["easy"] >= 36 and clues["medium"] >= 30
    assert clues["easy"] > clues["hard"]


def test_solution_is_a_valid_sudoku():
    _, sol = generate(11, "hard")
    for i in range(81):
        assert all(sol[p] != sol[i] for p in PEERS[i])


def test_logic_never_places_a_wrong_digit():
    rng = random.Random(3)
    for _ in range(12):
        diff = rng.choice(DIFFICULTIES)
        puzzle, solution = generate(rng.randint(1, 999_999), diff)
        logic = Logic(puzzle)
        while not logic.solved():
            step = logic.step(3)
            assert step is not None, "rated puzzles are solvable by the logic"
            if step.place:
                cell, d = step.place
                assert solution[cell] == d
            for cell, d in step.eliminate:
                assert solution[cell] != d, f"{step.technique} removed the answer"
            logic.apply(step)
        assert logic.grid == solution


def test_set_clear_notes_undo():
    g = Sudoku(42, "easy")
    cell = g.puzzle.index(0)
    peer = next(p for p in PEERS[cell] if not g.puzzle[p])
    d = g.solution[cell]
    assert g.toggle_note(peer, d) and g.notes[peer] >> d & 1
    assert g.set_value(cell, d)
    assert g.values[cell] == d and not g.notes[peer] >> d & 1  # cleaned up
    assert not g.set_value(cell, d)  # nothing changes
    given = next(i for i in range(81) if g.puzzle[i])
    assert not g.set_value(given, 1) and not g.clear(given)
    wrong = d % 9 + 1
    assert g.set_value(cell, wrong) and g.wrong(cell) and g.mistakes == 1
    assert g.undo() and g.values[cell] == d
    assert g.undo() and g.values[cell] == 0 and g.notes[peer] >> d & 1
    assert g.clear(peer) and g.notes[peer] == 0


def test_conflicts():
    g = Sudoku(42, "easy")
    cell = g.puzzle.index(0)
    peer_given = next(p for p in PEERS[cell] if g.puzzle[p])
    g.set_value(cell, g.puzzle[peer_given])
    assert {cell, peer_given} <= g.conflicts()


def test_fill_notes_and_restart():
    g = Sudoku(42, "medium")
    assert g.fill_notes()
    for i in range(81):
        if g.values[i]:
            assert g.notes[i] == 0
        else:
            assert g.notes[i] >> g.solution[i] & 1
    assert not g.fill_notes()
    cell = g.puzzle.index(0)
    g.set_value(cell, g.solution[cell])
    assert g.restart() and g.values == g.puzzle and not any(g.notes)
    assert g.undo() and g.values[cell] == g.solution[cell]


def test_hint_mistake_then_logic_then_solved():
    g = Sudoku(7, "hard")
    cell = g.puzzle.index(0)
    g.set_value(cell, g.solution[cell] % 9 + 1)
    step = g.hint()
    assert step.technique == "mistake" and step.cells == [cell]
    g.clear(cell)
    # Following the hints solves the whole puzzle, every one correct.
    for _ in range(81):
        step = g.hint()
        if step is None:
            break
        assert step.place and g.solution[step.place[0]] == step.place[1]
        assert step.why
        g.set_value(*step.place, hint=True)
    assert g.is_solved() and g.mistakes == 1 and g.hints > 0


def test_hint_respects_notes_that_rule_out_the_answer_safely():
    g = Sudoku(7, "expert")
    cell = g.puzzle.index(0)
    g.toggle_note(cell, g.solution[cell] % 9 + 1)  # a wrong-only note: ignored
    step = g.hint()
    assert step.place and g.solution[step.place[0]] == step.place[1]


def test_save_roundtrip_and_replay():
    g = Sudoku(1234, "medium")
    empties = [i for i in range(81) if not g.puzzle[i]]
    g.elapsed = 3
    g.set_value(empties[0], g.solution[empties[0]])
    g.toggle_note(empties[1], 4)
    g.fill_notes()
    g.set_value(empties[2], g.solution[empties[2]] % 9 + 1)
    g.undo()
    g.clear(empties[0])
    g.restart()
    g.undo()
    step = g.hint()
    g.set_value(*step.place, hint=True)
    g.elapsed = 50
    copy = Sudoku.from_dict(g.to_dict())
    assert copy.values == g.values and copy.notes == g.notes and copy.log == g.log
    assert (copy.mistakes, copy.hints, copy.elapsed) == (g.mistakes, g.hints, g.elapsed)
    again = Sudoku.replay(g.seed, g.difficulty, grid_text(g.puzzle), grid_text(g.solution), g.log)
    assert again.values == g.values and again.notes == g.notes
    assert (again.mistakes, again.hints) == (g.mistakes, g.hints)
    assert g.log[-1].startswith("h") and "@" in g.log[-1]


def test_review_spots_guesses_clashes_and_wrong_digits():
    from sudoku_app.review import review_lines

    g = Sudoku(723983, "easy")
    cell = g.puzzle.index(0)
    given_peer = next(p for p in PEERS[cell] if g.puzzle[p])
    g.set_value(cell, g.puzzle[given_peer])  # clashes with a given digit
    lines = "\n".join(review_lines(g.seed, "easy", grid_text(g.puzzle), grid_text(g.solution), g.log, False))
    assert "clashed with a digit" in lines and "Still wrong on the board" in lines
    assert "Every action" in lines


def test_save_carries_race_progress():
    g = Sudoku(42, "easy")
    assert g.to_dict()["progress"] == sum(1 for v in g.puzzle if v)
