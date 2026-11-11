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
    parse_grid,
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
    assert clues["beginner"] >= 45 and clues["easy"] >= 36 and clues["medium"] >= 30
    assert clues["beginner"] > clues["easy"] > clues["hard"]


def test_old_levels_keep_their_puzzles():
    # v1.4.0 added beginner and master; puzzle numbers (and chat battles, which play
    # easy) must give the same puzzles as before.
    assert grid_text(generate(1, "easy")[0]) == (
        "600098000014070809500000010025360108300804002809017450080000005102080940000920001")
    assert grid_text(generate(1, "medium")[0]) == (
        "309000510005090040200300006608000000502106407000000109100009003080020700027000604")
    assert grid_text(generate(1, "hard")[0]) == (
        "000010000905400060041000908400001620500000009026900005304000710060004302000050000")


MASTER = ("_skyscraper", "_kite", "_empty_rectangle", "_xyz_wing", "_w_wing", "_unique_rectangle",
          "_naked_quad", "_hidden_quad", "_swordfish", "_finned_x_wing", "_finned_swordfish",
          "_jellyfish", "_bug")


def test_master_patterns_never_remove_the_answer():
    # Strip puzzles down to a minimum and, at every state on the way to the answer,
    # try every master pattern: none may ever remove the digit that belongs there.
    seen = set()
    for seed in range(1, 13):
        puzzle, solution = generate(seed, "hard")
        rng = random.Random(seed)
        cells = [i for i in range(81) if puzzle[i]]
        rng.shuffle(cells)
        for c in cells:
            keep, puzzle[c] = puzzle[c], 0
            if count_solutions(puzzle) != 1:
                puzzle[c] = keep
        logic = Logic(puzzle)
        while not logic.solved():
            for name in MASTER:
                step = getattr(logic, name)()
                if step is not None:
                    seen.add(step.technique)
                    assert step.level == 5 and step.why and step.cells
                    assert all(solution[c] != d for c, d in step.eliminate), (name, seed, step.why)
            step = logic.step()
            if step is None:
                break
            logic.apply(step)
    assert {"skyscraper", "kite", "empty rectangle", "XYZ-wing", "W-wing", "unique rectangle",
            "naked quad", "hidden quad", "swordfish", "finned X-wing", "finned swordfish",
            "jellyfish"} <= seen


def test_bug_plus_one():
    # A real state from a stripped puzzle: every open cell has two options except
    # row 2, column 7 (4, 7 or 9); the 7 fits three times around it, so it is the 7.
    logic = Logic(parse_grid("830750062102368050560290083285619300943872615716543829428137596350086201601025038"))
    logic.cand = [0, 0, 528, 0, 0, 18, 514, 0, 0, 0, 640, 0, 0, 0, 0, 656, 0, 144, 0, 0, 144, 0, 0, 18, 130,
                  0, 0, 0, 0, 0, 0, 0, 0, 0, 144, 144] + [0] * 29 + [640, 528, 0, 0, 0, 144, 0, 0, 640, 0,
                  528, 0, 0, 144, 0, 0]
    step = logic._bug()
    assert step is not None and step.technique == "BUG+1"
    assert sorted(step.eliminate) == [(15, 4), (15, 9)] and "it is the 7" in step.why
    logic.cand[2] = 1 << 4 | 1 << 9 | 1 << 1  # a second cell with three options: no BUG
    assert logic._bug() is None


def test_master_needs_a_master_pattern_and_expert_does_not():
    for seed in (1, 2):
        assert rate(generate(seed, "master")[0]) == 5
        assert rate(generate(seed, "expert")[0]) == 4


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
            step = logic.step()
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


def test_review_counts_pointing_and_pairs_as_proof():
    from sudoku_app.review import _forced, review_lines

    g = Sudoku(1, "hard")
    cell = next(c for c in range(81) if not g.values[c]
                and _forced(g.values, g.solution, c, g.solution[c])[0] == "logic")
    g.set_value(cell, g.solution[cell])
    lines = "\n".join(review_lines(g.seed, "hard", grid_text(g.puzzle), grid_text(g.solution), g.log, False))
    assert "1 proven with pointing, pairs, wings or a short chain" in lines
    assert "Found with more than a single" in lines and "without proof" not in lines


def test_review_names_the_missing_note():
    from sudoku_app.review import _forced, review_lines

    g = Sudoku(5, "easy")
    cell = next(c for c in range(81) if not g.values[c]
                and _forced(g.values, g.solution, c, g.solution[c])[0] == "choice")
    right = g.solution[cell]
    g.toggle_note(cell, right)  # the note leaves out the other options
    g.set_value(cell, right)
    lines = "\n".join(review_lines(g.seed, "easy", grid_text(g.puzzle), grid_text(g.solution), g.log, False))
    assert "your notes showed one option while more were still possible" in lines
    assert f"your notes here said only {right}, but" in lines
    assert "A missing note looks exactly like proof" in lines


def test_review_counts_a_short_chain_as_proof():
    from sudoku_app.review import _forced, review_lines

    # Expert #651828 at 41:30: the 3 at r9c2 needs an XY-wing, then a single
    # elsewhere, then it is the only spot. Before v1.3.1 the review called it a choice.
    g = Sudoku(651828, "expert")
    done = parse_grid("815630200000805306600920508561298437040060850708453601287549163100386700006002980")
    for c in range(81):
        if done[c] and not g.values[c]:
            g.set_value(c, done[c])
    how, _, proof, _ = _forced(g.values, g.solution, 73, 3)
    assert how == "chain" and proof.technique == "3 steps" and proof.why.startswith("XY-wing, then ")
    g.set_value(73, 3)
    lines = "\n".join(review_lines(g.seed, "expert", grid_text(g.puzzle), grid_text(g.solution), g.log, True))
    assert "r9c2 = 3  (proven in 3 steps: XY-wing, then " in lines


def test_short_chain_stays_short():
    from sudoku_app.review import _forced

    # A digit that only follows after many steps is still a choice.
    g = Sudoku(651828, "expert")
    far = [c for c in range(81) if not g.values[c]
           and _forced(g.values, g.solution, c, g.solution[c])[0] == "choice"]
    assert far


def test_save_carries_race_progress():
    g = Sudoku(42, "easy")
    assert g.to_dict()["progress"] == sum(1 for v in g.puzzle if v)


def test_clearing_a_digit_puts_its_notes_back():
    g = Sudoku(42, "easy")
    g.fill_notes()
    cell = g.puzzle.index(0)
    own = g.notes[cell]
    d = next(x for x in range(1, 10) if own >> x & 1 and x != g.solution[cell])  # a wrong digit
    peers_with = [p for p in PEERS[cell] if g.notes[p] >> d & 1]
    assert peers_with
    g.set_value(cell, d)
    assert all(not g.notes[p] >> d & 1 for p in peers_with)
    g.clear(cell)
    assert g.notes[cell] == own
    assert all(g.notes[p] >> d & 1 for p in peers_with)


def test_auto_notes_off_keeps_notes_and_replays():
    g = Sudoku(42, "easy")
    g.fill_notes()
    cell = g.puzzle.index(0)
    d = g.solution[cell]
    peers_with = [p for p in PEERS[cell] if g.notes[p] >> d & 1]
    g.set_value(cell, d, clean_notes=False)
    assert g.log[-1].split("@")[0].endswith("!")
    assert all(g.notes[p] >> d & 1 for p in peers_with)
    again = Sudoku.replay(g.seed, g.difficulty, grid_text(g.puzzle), grid_text(g.solution), g.log)
    assert again.notes == g.notes and again.values == g.values
    g.restart()
    assert g.removed == {}
