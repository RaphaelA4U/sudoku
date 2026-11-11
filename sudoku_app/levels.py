"""The Levels page: what each difficulty needs, how a puzzle gets its level, and
why chains (guessing in your head) are never needed. Also: what one puzzle needed."""

from __future__ import annotations

import textwrap
from collections import Counter

from .game import CLUE_FLOOR, DIFFICULTIES, LEVELS, Logic

ACCENT = "#875fff"
WINC = "#4dd0e1"

# Every technique of the solver, by the level it belongs to, with one line on how it works.
TECHNIQUES: dict[int, list[tuple[str, str]]] = {
    1: [("hidden single in a box", "in this box the 5 fits in only one cell")],
    2: [("naked single", "only one digit is left for this cell"),
        ("hidden single in a row or column", "in this row the 5 fits in only one cell")],
    3: [("pointing", "in a box the 5 only fits on one row, so the rest of that row cannot have a 5"),
        ("box/line", "in a row the 5 only fits inside one box, so the rest of that box cannot have a 5"),
        ("naked pair", "two cells of a row, column or box both have only 2 and 7: the other cells "
                       "of that row, column or box cannot have them"),
        ("hidden pair", "2 and 7 fit only in the same two cells of a row, column or box: those "
                        "cells cannot hold anything else")],
    4: [("naked and hidden triple", "the pair, with three digits in three cells"),
        ("X-wing", "in two rows the 5 fits only in the same two columns: the rest of those "
                   "columns cannot have a 5"),
        ("XY-wing", "a cell with two options sees two others with two options; whichever it is, "
                    "one of the other two becomes the same digit, so cells that see both cannot be it")],
    5: [("skyscraper", "two rows where the 5 fits twice, lined up at one end: one of the other "
                       "ends is the 5"),
        ("kite", "a row and a column where the 5 fits twice, linked inside one box: one of the "
                 "far ends is the 5"),
        ("empty rectangle", "in a box the 5 only fits on a cross; with a row or column where it "
                            "fits twice, one spot would leave the box without a 5"),
        ("W-wing", "two cells with the same two options, linked by a row, column or box where "
                   "one of those digits fits twice: one of the two cells is the other digit"),
        ("XYZ-wing", "the XY-wing with three options in the middle cell"),
        ("unique rectangle", "four cells in two boxes that could swap their digits would give "
                             "two solutions; every puzzle has one, so that pattern cannot happen"),
        ("naked and hidden quad", "the pair, with four digits in four cells"),
        ("swordfish and jellyfish", "the X-wing over three or four rows"),
        ("finned X-wing and swordfish", "the same with one or two extra spots (the fin) in one "
                                        "box: only cells that also see the fin lose the 5"),
        ("BUG+1", "every open cell has two options except one with three: that one is the digit "
                  "that fits three times in its row")],
}

LEVEL_LINE = {
    "beginner": "Only the easiest single.",
    "easy": "All singles.",
    "medium": "All singles, with fewer digits.",
    "hard": "Pointing and pairs.",
    "expert": "Triples, X-wings and XY-wings.",
    "master": "Fixed patterns beyond expert.",
}

CHAINS = ("coloring", "X-chains", "XY-chains", "forcing chains", "almost locked sets",
          "Sue de Coq", "WXYZ-wing")


def master_solved(games: list[dict]) -> bool:
    """Solved a master puzzle without hints."""
    return any(g.get("won") and g.get("difficulty") == "master" and not g.get("hints") for g in games)


def levels_text(games: list[dict]) -> str:
    solved = Counter(g.get("difficulty") for g in games if g.get("won"))
    out = [
        "[b u]How a puzzle gets its level[/b u]",
        "  The game solves every puzzle itself, the way a person would: always the",
        "  easiest step first. The level is the hardest kind of step it needed, even",
        "  if only once. So an expert puzzle needs at least one triple, X-wing or",
        "  XY-wing, and never anything from master. How many digits you start with",
        "  only counts for beginner, easy and medium, where more digits really make",
        "  it easier. From hard up the steps decide: a puzzle with 25 digits can be",
        "  easier than one with 30.",
        "",
    ]
    for diff in DIFFICULTIES:
        hi = LEVELS[diff][1]
        floor = CLUE_FLOOR[diff]
        head = f"[b {ACCENT}]{diff.upper()}[/]  {LEVEL_LINE[diff]}"
        extra = []
        if floor:
            extra.append(f"at least {floor} digits to start")
        if solved[diff]:
            extra.append(f"[{WINC}]you solved {solved[diff]}[/]")
        if extra:
            head += "  [dim](" + ", ".join(extra) + ")[/dim]"
        out.append(head)
        if diff == "medium":
            out.append("  Nothing new to learn: the singles, with fewer digits to start with. Now")
            out.append("  and then a puzzle also needs a pointing or a pair (see hard).")
        else:
            for name, how in TECHNIQUES[hi]:
                text = textwrap.fill(f"{name}: {how}.", 74, initial_indent="  ", subsequent_indent="    ")
                out.append(text.replace(f"{name}:", f"[b]{name}[/b]:", 1))
        out.append("")
    out += [
        "[b u]Never: guessing and chains[/b u]",
        textwrap.fill("No level ever needs " + ", ".join(CHAINS[:-1]) + " or " + CHAINS[-1] + ".", 76,
                      initial_indent="  ", subsequent_indent="  "),
        "  Those ask you to fill in a digit in your head and follow it until",
        "  something breaks: close to guessing. Every puzzle has exactly one",
        "  solution and the steps above always reach it; [b]h[/b] shows the next one.",
        "",
    ]
    if master_solved(games):
        out += [f"  [{WINC}]You solved a master puzzle without hints. Want chains anyway?[/]",
                "  Then you may ask: github.com/RaphaelA4U/sudoku/issues"]
    else:
        out += ["  Want chains anyway? Solve a master puzzle without hints first,",
                "  then this page tells you where to ask. ;)"]
    return "\n".join(out)


def needed(puzzle: list[int]) -> list[str]:
    """What solving this puzzle takes, hardest kind first: 'skyscraper (2)', 'single (41)'."""
    logic = Logic(puzzle)
    count: Counter = Counter()
    level: dict[str, int] = {}
    while not logic.solved():
        step = logic.step()
        if step is None:
            break
        name = "single" if step.technique.endswith("single") else step.technique
        count[name] += 1
        level[name] = max(level.get(name, 0), step.level)
        logic.apply(step)
    order = sorted(count, key=lambda n: (-level[n], n))
    return [f"{n} ({count[n]})" for n in order]
