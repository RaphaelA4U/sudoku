"""Move-by-move review of a sudoku game, built from its action log.

For every digit you filled in it checks, against the board at that moment
(counting only the digits that were right), whether the digit was forced
(a single: the only digit for that cell, or the only cell for that digit
in a row, column or box), proven by a technique such as pointing or a pair,
or a choice between several options, and whether it clashed with a digit
that was already visible. For a choice it also checks your notes: when they
showed one option while more were still possible, it says which note was
missing, because a missing note looks exactly like proof.
"""

from __future__ import annotations

from typing import Optional

from .game import COL, PEERS, ROW, UNITS, Logic, Step, Sudoku, digits, parse_grid, unit_name

HINT = "#ff9e3d"
WINC = "#4dd0e1"


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _rc(cell: int) -> str:
    return f"r{ROW[cell] + 1}c{COL[cell] + 1}"


# Techniques that only remove candidates (no digit placed), easiest first.
ELIMINATORS = ("_pointing", "_claiming", "_naked_pair", "_hidden_pair", "_naked_triple",
               "_hidden_triple", "_x_wing", "_xy_wing", "_swordfish")


def _single(logic: Logic, cell: int, d: int) -> str:
    """'naked', 'hidden <unit>' or '' when d is not (yet) a single in this cell."""
    if logic.cand[cell] == 1 << d:
        return "naked"
    for kind, idx, unit in UNITS:
        if cell in unit and [i for i in unit if logic.cand[i] >> d & 1] == [cell]:
            return f"hidden {kind} {idx + 1}"
    return ""


def _forced(values: list[int], solution: list[int], cell: int,
            d: int) -> tuple[str, list[int], Optional[Step], Logic]:
    """(kind, options, step, logic). kind is 'naked', 'hidden <unit>', 'logic'
    (a single once pointing, pairs or another technique has removed candidates;
    step is the one that made it a single), 'choice' or 'impossible'. For a
    choice, options and logic are what is still possible after every technique."""
    clean = [v if v == solution[i] else 0 for i, v in enumerate(values)]
    clean[cell] = 0
    logic = Logic(clean)
    options = digits(logic.cand[cell])
    if d not in options:
        return "impossible", options, None, logic
    single = _single(logic, cell, d)
    if single:
        return single, options, None, logic
    for _ in range(60):
        step = None
        for name in ELIMINATORS:
            step = getattr(logic, name)()
            if step is not None:
                break
        if step is None:
            break
        logic.apply(step)
        if not logic.cand[cell] >> d & 1:
            break
        if _single(logic, cell, d):
            return "logic", options, step, logic
    return "choice", digits(logic.cand[cell]) or options, None, logic


def _note_gaps(game: Sudoku, solution: list[int], logic: Logic, cell: int, d: int) -> list[str]:
    """Where your notes said this digit was the only option while it was not."""
    gaps = []
    for kind, idx, unit in sorted(UNITS, key=lambda u: u[0] != "box"):
        if cell not in unit:
            continue
        noted = [i for i in unit if not game.values[i] and game.notes[i] >> d & 1]
        missing = [i for i in unit if i != cell and not game.values[i] and game.notes[i]
                   and not game.notes[i] >> d & 1 and logic.cand[i] >> d & 1]
        if noted == [cell] and missing:
            gaps.append(f"your notes had the {d} only here in {unit_name(kind, idx)}, but "
                        f"{_join([_rc(i) for i in missing])} could also hold a {d}")
            break
    own = digits(game.notes[cell])
    others = [x for x in digits(logic.cand[cell]) if x != d]
    if not gaps and own == [d] and others:
        gap = f"your notes here said only {d}, but {_join(others)} could also go here"
        blockers = [p for x in others for p in PEERS[cell] if game.values[p] == x and solution[p] != x]
        if blockers:
            gap += (" (ruled out only by your wrong " +
                    _join([f"{_rc(p)} = {game.values[p]}" for p in blockers]) + ")")
        gaps.append(gap)
    return gaps


def _join(items: list) -> str:
    items = [str(x) for x in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _clashes(values: list[int], solution: list[int], cell: int, d: int) -> list[int]:
    """Cells that already show this digit, counting only digits that are right
    (a clash with your own earlier mistake is that mistake's fault)."""
    return [p for p in PEERS[cell] if values[p] == d and solution[p] == d]


def review_lines(seed: int, difficulty: str, puzzle: str, solution: str, log: list[str],
                 won: bool) -> list[str]:
    sol = parse_grid(solution)
    game = Sudoku(seed, difficulty, puzzle=parse_grid(puzzle), solution=sol)
    rows: list[str] = []
    placed = forced = choices = choices_wrong = wrong = hints = undos = notes = 0
    clash_lines: list[str] = []
    proven: list[str] = []
    note_lines: list[str] = []
    pauses: list[tuple[int, int, str]] = []
    prev_clock = 0
    for n, entry in enumerate(log):
        action, _, clock_s = entry.partition("@")
        clock = int(clock_s or 0)
        think = clock - prev_clock
        prev_clock = clock
        kind, rest = action[0], action[1:]
        text, style = action, ""
        if kind in "vh":
            cell, d = (int(x) for x in rest.rstrip("!").split("="))
            how, options, proof, logic = _forced(game.values, sol, cell, d)
            clash = _clashes(game.values, sol, cell, d)
            ok = d == sol[cell]
            placed += 1
            if kind == "h":
                hints += 1
                text = f"{_rc(cell)} = {d}  (hint)"
                style = f"{HINT}"
            else:
                if how.startswith(("naked", "hidden")):
                    forced += 1
                    why = "only digit that fits" if how == "naked" else f"only spot in {how[7:]}"
                elif how == "logic" and proof is not None:
                    forced += 1
                    why = f"proven with {proof.technique}"
                    proven.append(f"{_rc(cell)} = {d} at {_fmt_time(clock)}, {proof.technique}: {proof.why}")
                elif how == "choice":
                    choices += 1
                    choices_wrong += 0 if ok else 1
                    why = "a choice between " + " and ".join(str(o) for o in options)
                    gaps = _note_gaps(game, sol, logic, cell, d)
                    if gaps:
                        why += "; " + "; ".join(gaps)
                        note_lines.append(f"{_rc(cell)} = {d} at {_fmt_time(clock)} "
                                          f"({'right' if ok else 'wrong'}): " + "; ".join(gaps))
                else:
                    why = "cannot go there"
                if clash:
                    why += ", clashes with " + ", ".join(_rc(p) for p in clash)
                    clash_lines.append(f"{_rc(cell)} = {d} at {_fmt_time(clock)}: there was already a {d} "
                                       f"in {', '.join(_rc(p) for p in clash)}")
                text = f"{_rc(cell)} = {d}  ({why})"
                if not ok:
                    wrong += 1
                    text += f"  wrong, it is {sol[cell]}"
                    style = "red"
            if think >= 20:
                pauses.append((think, clock, f"{_rc(cell)} = {d}"))
        elif kind == "c":
            cell = int(rest)
            text, style = f"clear {_rc(cell)} (was {game.values[cell] or 'notes'})", "dim"
        elif kind == "n":
            cell, d = (int(x) for x in rest.split("="))
            notes += 1
            on = not game.notes[cell] >> d & 1
            text, style = f"note {d} {'in' if on else 'out of'} {_rc(cell)}", "dim"
        elif kind == "N":
            text, style = "fill in every note", "dim"
        elif kind == "R":
            text, style = "start over", "dim"
        elif kind == "u":
            undos += 1
            text, style = "undo", "dim"
        line = f"  {n + 1:>3}  {_fmt_time(clock):>5}  {text}"
        rows.append(f"[{style}]{line}[/]" if style else line)
        game = Sudoku.replay(seed, difficulty, puzzle, solution, log, upto=n + 1)

    total = prev_clock
    still_wrong = [i for i in range(81) if game.wrong(i)]
    empty = sum(1 for v in game.values if not v)
    status = f"[bold {WINC}]Solved[/]" if won or game.is_solved() else f"Not solved ({empty} empty)"
    lines = [
        f"[b]Puzzle #{seed}[/b]  {difficulty}",
        f"  {status} in {_fmt_time(total)}. {placed} digits filled in, {wrong} wrong, "
        f"{hints} hint{'s' if hints != 1 else ''}, {undos} undo{'s' if undos != 1 else ''}, "
        f"{notes} note{'s' if notes != 1 else ''}.",
        "",
        "[b u]How it went[/b u]",
        f"  {forced} of {placed - hints} digits were forced (you could be sure)"
        + (f": {forced - len(proven)} singles and {len(proven)} proven with pointing, pairs or another "
           "technique." if proven else ", all of them singles."),
    ]
    if proven:
        lines.append("  Found with more than a single:")
        lines += [f"    {p}" for p in proven]
    if choices:
        lines.append(f"  {choices} time{'s' if choices != 1 else ''} you picked between options without proof; "
                     f"{choices_wrong} of those were wrong.")
    if note_lines:
        lines.append(f"  {len(note_lines)} time{'s' if len(note_lines) != 1 else ''} your notes showed one "
                     "option while more were still possible:")
        lines += [f"    {c}" for c in note_lines]
    if clash_lines:
        lines.append(f"  {len(clash_lines)} digit{'s' if len(clash_lines) != 1 else ''} clashed with a digit "
                     "that was already on the board:")
        lines += [f"    {c}" for c in clash_lines]
    if still_wrong:
        lines.append("  Still wrong on the board: " + ", ".join(
            f"[red]{_rc(i)} = {game.values[i]}[/] (should be {sol[i]})" for i in still_wrong) + ".")
    if pauses:
        top = sorted(pauses, reverse=True)[:3]
        lines.append("  Longest thinking: " + ", ".join(f"{s} s before {what}" for s, _, what in top) + ".")
    lines += ["", "[b u]Tips for next time[/b u]"]
    tips = []
    if choices and difficulty in ("easy", "medium"):
        tips.append("Do not pick between two options. On easy and medium there is always a cell somewhere "
                    "where only one digit fits, or a digit with only one spot in a row, column or box. "
                    "Look for that one instead.")
    elif choices:
        tips.append("Do not pick between options. There is always a proof, but on hard it can take pointing "
                    "or a pair (on expert also triples, X-wings and XY-wings). Look for those first; "
                    "h shows the next step.")
    if note_lines:
        tips.append("A missing note looks exactly like proof. Before you leave a digit out of your notes, "
                    "be sure why: a digit in its row, column or box, or pointing where ALL spots of that "
                    "digit in a box lie on one line.")
    if clash_lines:
        tips.append("Before you fill in a digit, check its row, its column and its box for that digit.")
    if notes and choices:
        tips.append("Notes with two options are a reminder, not a decision: wait until one of them is "
                    "ruled out by a digit elsewhere.")
    if not tips:
        tips.append("Clean game. To get faster: scan digit by digit (all the 1s, then all the 2s...) "
                    "and start with the digit that is already on the board most often.")
    lines += [f"  - {t}" for t in tips]
    lines += ["", "[b u]Every action[/b u]"] + rows
    return lines
