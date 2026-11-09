"""Sudoku rules engine: the single source of truth for the game.

- Puzzles are numbered: the same number and difficulty always give the same
  puzzle. Every puzzle has exactly one solution.
- A small logical solver (the way people solve: singles, pairs, pointing,
  X-wings...) rates the difficulty and explains hints.
- The game keeps every action in a log, so a finished game can be replayed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Optional

DIFFICULTIES = ["easy", "medium", "hard", "expert"]
DIFFICULTY_INFO = {
    "easy": "Plenty of digits to start with. Looking around is enough.",
    "medium": "Fewer digits. Still only needs 'where can this digit go'.",
    "hard": "Needs pairs and pointing: notes help a lot.",
    "expert": "Needs X-wings, triples and XY-wings. Notes are a must.",
}

# Removal limits and the logic level a difficulty needs (see Logic below).
CLUE_FLOOR = {"easy": 36, "medium": 30, "hard": 0, "expert": 0}
LEVELS = {"easy": (1, 1), "medium": (1, 2), "hard": (2, 2), "expert": (3, 3)}

ALL = 0x3FE  # bits 1..9
UNDO_LIMIT = 1000
SAVED_UNDO_LIMIT = 300

ROW = [i // 9 for i in range(81)]
COL = [i % 9 for i in range(81)]
BOX = [(i // 27) * 3 + (i % 9) // 3 for i in range(81)]
ROWS = [[r * 9 + c for c in range(9)] for r in range(9)]
COLS = [[r * 9 + c for r in range(9)] for c in range(9)]
BOXES = [[(b // 3) * 27 + (b % 3) * 3 + r * 9 + c for r in range(3) for c in range(3)] for b in range(9)]
UNITS = [("row", i, u) for i, u in enumerate(ROWS)] + [("column", i, u) for i, u in enumerate(COLS)] + [
    ("box", i, u) for i, u in enumerate(BOXES)
]
PEERS = [sorted(set(ROWS[ROW[i]] + COLS[COL[i]] + BOXES[BOX[i]]) - {i}) for i in range(81)]
POP = [bin(m).count("1") for m in range(1 << 10)]


def digits(mask: int) -> list[int]:
    return [d for d in range(1, 10) if mask >> d & 1]


def cell_name(cell: int) -> str:
    return f"row {ROW[cell] + 1}, column {COL[cell] + 1}"


def unit_name(kind: str, idx: int) -> str:
    return f"{kind} {idx + 1}"


def parse_grid(text: str) -> list[int]:
    vals = [int(ch) if ch.isdigit() else 0 for ch in text if ch.isdigit() or ch in ".0"]
    if len(vals) != 81:
        raise ValueError("a grid needs 81 cells")
    return vals


def grid_text(grid: list[int]) -> str:
    return "".join(str(v) for v in grid)


# ---------------------------------------------------------------------------
# Brute force: counting solutions and making full grids
# ---------------------------------------------------------------------------


def _search(grid: list[int], limit: int, rng: Optional[random.Random] = None) -> tuple[int, Optional[list[int]]]:
    """(number of solutions up to `limit`, the first one found)."""
    g = list(grid)
    rows, cols, boxes = [0] * 9, [0] * 9, [0] * 9
    for i, v in enumerate(g):
        if v:
            bit = 1 << v
            if rows[ROW[i]] & bit or cols[COL[i]] & bit or boxes[BOX[i]] & bit:
                return 0, None
            rows[ROW[i]] |= bit
            cols[COL[i]] |= bit
            boxes[BOX[i]] |= bit
    empties = [i for i in range(81) if not g[i]]
    count = 0
    first: Optional[list[int]] = None

    def rec() -> None:
        nonlocal count, first
        best, best_mask, best_n = -1, 0, 10
        for i in empties:
            if g[i]:
                continue
            m = ALL & ~(rows[ROW[i]] | cols[COL[i]] | boxes[BOX[i]])
            n = POP[m]
            if n == 0:
                return
            if n < best_n:
                best, best_mask, best_n = i, m, n
                if n == 1:
                    break
        if best < 0:
            count += 1
            if first is None:
                first = list(g)
            return
        opts = digits(best_mask)
        if rng is not None:
            rng.shuffle(opts)
        r, c, b = ROW[best], COL[best], BOX[best]
        for v in opts:
            bit = 1 << v
            g[best] = v
            rows[r] |= bit
            cols[c] |= bit
            boxes[b] |= bit
            rec()
            rows[r] &= ~bit
            cols[c] &= ~bit
            boxes[b] &= ~bit
            g[best] = 0
            if count >= limit:
                return

    rec()
    return count, first


def count_solutions(grid: list[int], limit: int = 2) -> int:
    return _search(grid, limit)[0]


def solve(grid: list[int]) -> Optional[list[int]]:
    count, first = _search(grid, 1)
    return first if count else None


def random_full_grid(rng: random.Random) -> list[int]:
    _, grid = _search([0] * 81, 1, rng)
    assert grid is not None
    return grid


# ---------------------------------------------------------------------------
# Logic: how a person solves it. Used for hints and for rating difficulty.
# ---------------------------------------------------------------------------


@dataclass
class Step:
    level: int                        # 1 singles, 2 pairs/pointing, 3 triples/wings
    technique: str
    place: Optional[tuple[int, int]] = None           # (cell, digit)
    eliminate: list[tuple[int, int]] = field(default_factory=list)
    cells: list[int] = field(default_factory=list)    # the cells that prove it
    why: str = ""


class Logic:
    def __init__(self, grid: list[int]) -> None:
        self.grid = list(grid)
        self.cand = [0] * 81
        for i in range(81):
            if not self.grid[i]:
                used = 0
                for p in PEERS[i]:
                    used |= 1 << self.grid[p]
                self.cand[i] = ALL & ~used

    # -- applying --------------------------------------------------------
    def place(self, cell: int, d: int) -> None:
        self.grid[cell] = d
        self.cand[cell] = 0
        bit = 1 << d
        for p in PEERS[cell]:
            self.cand[p] &= ~bit

    def apply(self, step: Step) -> None:
        if step.place:
            self.place(*step.place)
        for cell, d in step.eliminate:
            self.cand[cell] &= ~(1 << d)

    def solved(self) -> bool:
        return all(self.grid)

    def broken(self) -> bool:
        return any(not self.grid[i] and not self.cand[i] for i in range(81))

    # -- finding the easiest next step ------------------------------------
    def step(self, max_level: int = 3) -> Optional[Step]:
        finders = [self._hidden_single_box, self._naked_single, self._hidden_single_line]
        if max_level >= 2:
            finders += [self._pointing, self._claiming, self._naked_pair, self._hidden_pair]
        if max_level >= 3:
            finders += [self._naked_triple, self._hidden_triple, self._x_wing, self._xy_wing, self._swordfish]
        for find in finders:
            step = find()
            if step is not None:
                return step
        return None

    def _places(self, unit: list[int], d: int) -> list[int]:
        bit = 1 << d
        return [i for i in unit if self.cand[i] & bit]

    def _hidden_single(self, kinds: tuple[str, ...]) -> Optional[Step]:
        for kind, idx, unit in UNITS:
            if kind not in kinds:
                continue
            for d in range(1, 10):
                spots = self._places(unit, d)
                if len(spots) == 1:
                    return Step(1, "hidden single", place=(spots[0], d), cells=unit,
                                why=f"In {unit_name(kind, idx)} the {d} can only go here.")
        return None

    def _hidden_single_box(self) -> Optional[Step]:
        return self._hidden_single(("box",))

    def _hidden_single_line(self) -> Optional[Step]:
        return self._hidden_single(("row", "column"))

    def _naked_single(self) -> Optional[Step]:
        for i in range(81):
            if not self.grid[i] and POP[self.cand[i]] == 1:
                d = digits(self.cand[i])[0]
                return Step(1, "naked single", place=(i, d), cells=[i],
                            why=f"Only {d} fits in {cell_name(i)}: every other digit is already "
                                f"in its row, column or box.")
        return None

    def _pointing(self) -> Optional[Step]:
        """In a box, a digit only fits on one row/column: remove it from the rest of that line."""
        for b, box in enumerate(BOXES):
            for d in range(1, 10):
                spots = self._places(box, d)
                if len(spots) < 2:
                    continue
                for kind, key, lines in (("row", ROW, ROWS), ("column", COL, COLS)):
                    if len({key[s] for s in spots}) == 1:
                        line = key[spots[0]]
                        elim = [(i, d) for i in lines[line] if BOX[i] != b and self.cand[i] >> d & 1]
                        if elim:
                            return Step(2, "pointing", eliminate=elim, cells=spots,
                                        why=f"In box {b + 1} the {d} must be on {kind} {line + 1}, "
                                            f"so no other cell of that {kind} can have a {d}.")
        return None

    def _claiming(self) -> Optional[Step]:
        """On a line, a digit only fits inside one box: remove it from the rest of that box."""
        for kind, idx, unit in UNITS:
            if kind == "box":
                continue
            for d in range(1, 10):
                spots = self._places(unit, d)
                if len(spots) < 2 or len({BOX[s] for s in spots}) != 1:
                    continue
                b = BOX[spots[0]]
                elim = [(i, d) for i in BOXES[b] if i not in unit and self.cand[i] >> d & 1]
                if elim:
                    return Step(2, "box/line", eliminate=elim, cells=spots,
                                why=f"On {unit_name(kind, idx)} the {d} must be in box {b + 1}, "
                                    f"so the rest of that box cannot have a {d}.")
        return None

    def _naked_subset(self, size: int, level: int, name: str) -> Optional[Step]:
        from itertools import combinations

        for kind, idx, unit in UNITS:
            open_cells = [i for i in unit if not self.grid[i] and 2 <= POP[self.cand[i]] <= size]
            for combo in combinations(open_cells, size):
                mask = 0
                for i in combo:
                    mask |= self.cand[i]
                if POP[mask] != size:
                    continue
                elim = [(i, d) for i in unit if i not in combo and not self.grid[i]
                        for d in digits(self.cand[i] & mask)]
                if elim:
                    ds = " and ".join(str(d) for d in digits(mask))
                    return Step(level, name, eliminate=elim, cells=list(combo),
                                why=f"In {unit_name(kind, idx)}, {ds} must go in these {size} cells, "
                                    f"so the other cells of that {kind} cannot have them.")
        return None

    def _hidden_subset(self, size: int, level: int, name: str) -> Optional[Step]:
        from itertools import combinations

        for kind, idx, unit in UNITS:
            places = {d: self._places(unit, d) for d in range(1, 10)}
            options = [d for d, sp in places.items() if 2 <= len(sp) <= size]
            for combo in combinations(options, size):
                cells = sorted(set().union(*(places[d] for d in combo)))
                if len(cells) != size:
                    continue
                keep = 0
                for d in combo:
                    keep |= 1 << d
                elim = [(i, d) for i in cells for d in digits(self.cand[i] & ~keep)]
                if elim:
                    ds = " and ".join(str(d) for d in combo)
                    return Step(level, name, eliminate=elim, cells=cells,
                                why=f"In {unit_name(kind, idx)}, {ds} only fit in these {size} cells, "
                                    f"so those cells cannot hold anything else.")
        return None

    def _naked_pair(self) -> Optional[Step]:
        return self._naked_subset(2, 2, "naked pair")

    def _hidden_pair(self) -> Optional[Step]:
        return self._hidden_subset(2, 2, "hidden pair")

    def _naked_triple(self) -> Optional[Step]:
        return self._naked_subset(3, 3, "naked triple")

    def _hidden_triple(self) -> Optional[Step]:
        return self._hidden_subset(3, 3, "hidden triple")

    def _fish(self, size: int, name: str) -> Optional[Step]:
        from itertools import combinations

        for d in range(1, 10):
            for base, cover, key, other in (("row", "column", COL, COLS), ("column", "row", ROW, ROWS)):
                lines = ROWS if base == "row" else COLS
                candidates = []
                for li, line in enumerate(lines):
                    spots = self._places(line, d)
                    if 2 <= len(spots) <= size:
                        candidates.append((li, spots))
                for combo in combinations(candidates, size):
                    covered = sorted({key[s] for _, spots in combo for s in spots})
                    if len(covered) != size:
                        continue
                    used = {s for _, spots in combo for s in spots}
                    elim = [(i, d) for ci in covered for i in other[ci]
                            if i not in used and self.cand[i] >> d & 1]
                    if elim:
                        bases = ", ".join(str(li + 1) for li, _ in combo)
                        covers = ", ".join(str(c + 1) for c in covered)
                        return Step(3, name, eliminate=elim, cells=sorted(used),
                                    why=f"The {d}s of {base}s {bases} all sit in {cover}s {covers}, "
                                        f"so the rest of those {cover}s cannot have a {d}.")
        return None

    def _x_wing(self) -> Optional[Step]:
        return self._fish(2, "X-wing")

    def _swordfish(self) -> Optional[Step]:
        return self._fish(3, "swordfish")

    def _xy_wing(self) -> Optional[Step]:
        pairs = [i for i in range(81) if not self.grid[i] and POP[self.cand[i]] == 2]
        peers = [set(p) for p in PEERS]
        for pivot in pairs:
            x, y = digits(self.cand[pivot])
            wings = [i for i in pairs if i in peers[pivot] and i != pivot]
            for a in wings:
                ca = self.cand[a]
                if not ca & (1 << x) or ca & (1 << y):
                    continue
                z_mask = ca & ~(1 << x)
                for b in wings:
                    if b == a:
                        continue
                    if self.cand[b] != ((1 << y) | z_mask):
                        continue
                    z = digits(z_mask)[0]
                    elim = [(i, z) for i in range(81) if i not in (pivot, a, b) and not self.grid[i]
                            and self.cand[i] >> z & 1 and i in peers[a] and i in peers[b]]
                    if elim:
                        return Step(3, "XY-wing", eliminate=elim, cells=[pivot, a, b],
                                    why=f"Whichever of {x} or {y} goes in {cell_name(pivot)}, one of the "
                                        f"two other marked cells becomes {z}, so cells that see both "
                                        f"cannot have a {z}.")
        return None


def rate(puzzle: list[int]) -> int:
    """The hardest logic level needed: 1, 2, 3, or 4 when logic alone gets stuck."""
    logic = Logic(puzzle)
    worst = 1
    while not logic.solved():
        step = logic.step(3)
        if step is None:
            return 4
        worst = max(worst, step.level)
        logic.apply(step)
    return worst


def _rate_at_most(puzzle: list[int], max_level: int) -> bool:
    logic = Logic(puzzle)
    while not logic.solved():
        step = logic.step(max_level)
        if step is None:
            return False
        logic.apply(step)
    return True


def generate(seed: int, difficulty: str) -> tuple[list[int], list[int]]:
    """(puzzle, solution) for this number and difficulty; always the same pair."""
    rng = random.Random(seed * 10 + DIFFICULTIES.index(difficulty))
    lo, hi = LEVELS[difficulty]
    floor = CLUE_FLOOR[difficulty]
    while True:
        solution = random_full_grid(rng)
        puzzle = list(solution)
        cells = list(range(41))  # symmetric pairs: (i, 80 - i)
        rng.shuffle(cells)
        clues = 81
        for cell in cells:
            pair = {cell, 80 - cell}
            if clues - len(pair) < floor:
                continue
            saved = {i: puzzle[i] for i in pair}
            for i in pair:
                puzzle[i] = 0
            if count_solutions(puzzle) != 1 or (hi < 4 and not _rate_at_most(puzzle, hi)):
                for i, v in saved.items():
                    puzzle[i] = v
                continue
            clues -= len(pair)
        if rate(puzzle) >= lo:
            return puzzle, solution


# ---------------------------------------------------------------------------
# A game in progress
# ---------------------------------------------------------------------------


class Sudoku:
    def __init__(self, seed: int, difficulty: str = "easy",
                 puzzle: Optional[list[int]] = None, solution: Optional[list[int]] = None) -> None:
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"unknown difficulty: {difficulty}")
        self.seed = int(seed)
        self.difficulty = difficulty
        if puzzle is None or solution is None:
            puzzle, solution = generate(self.seed, difficulty)
        self.puzzle = list(puzzle)
        self.solution = list(solution)
        self.values = list(puzzle)
        self.notes = [0] * 81
        self.elapsed = 0.0
        self.started = False
        self.mistakes = 0        # digits placed that differ from the solution
        self.hints = 0           # hints that filled in a cell
        self.history: list[dict] = []
        # Every action in order, undos included: "v12=5" set, "c12" clear,
        # "n12=5" note toggle, "N" fill notes, "h12=5" hint, "R" restart,
        # "u" undo; "@n" = the clock at that moment.
        self.log: list[str] = []

    # -- queries ---------------------------------------------------------
    def given(self, cell: int) -> bool:
        return self.puzzle[cell] != 0

    def is_solved(self) -> bool:
        return self.values == self.solution

    def wrong(self, cell: int) -> bool:
        v = self.values[cell]
        return bool(v) and v != self.solution[cell]

    def conflicts(self) -> set[int]:
        bad: set[int] = set()
        for _, _, unit in UNITS:
            seen: dict[int, int] = {}
            for i in unit:
                v = self.values[i]
                if not v:
                    continue
                if v in seen:
                    bad.add(i)
                    bad.add(seen[v])
                else:
                    seen[v] = i
        return bad

    def count(self, d: int) -> int:
        return sum(1 for v in self.values if v == d)

    def candidates(self, cell: int) -> int:
        if self.values[cell]:
            return 0
        used = 0
        for p in PEERS[cell]:
            used |= 1 << self.values[p]
        return ALL & ~used

    def filled(self) -> int:
        return sum(1 for v in self.values if v)

    # -- undo / log ------------------------------------------------------
    def _state(self) -> dict[str, Any]:
        return {"values": grid_text(self.values), "notes": list(self.notes)}

    def _load_state(self, state: dict[str, Any]) -> None:
        self.values = parse_grid(state["values"])
        self.notes = [int(n) for n in state["notes"]]

    def _checkpoint(self) -> None:
        self.history.append(self._state())
        if len(self.history) > UNDO_LIMIT:
            del self.history[: len(self.history) - UNDO_LIMIT]
        self.started = True

    def _record(self, action: str) -> None:
        self.log.append(f"{action}@{int(self.elapsed)}")

    def can_undo(self) -> bool:
        return bool(self.history)

    def undo(self) -> bool:
        if not self.history:
            return False
        self._record("u")
        self._load_state(self.history.pop())
        return True

    # -- actions ---------------------------------------------------------
    def set_value(self, cell: int, d: int, hint: bool = False) -> bool:
        if self.given(cell) or not 1 <= d <= 9 or self.values[cell] == d:
            return False
        self._checkpoint()
        self._record(f"{'h' if hint else 'v'}{cell}={d}")
        self.values[cell] = d
        self.notes[cell] = 0
        bit = 1 << d  # the digit can no longer be a note in its row, column, box
        for p in PEERS[cell]:
            self.notes[p] &= ~bit
        if hint:
            self.hints += 1
        elif d != self.solution[cell]:
            self.mistakes += 1
        return True

    def clear(self, cell: int) -> bool:
        if self.given(cell) or (not self.values[cell] and not self.notes[cell]):
            return False
        self._checkpoint()
        self._record(f"c{cell}")
        self.values[cell] = 0
        self.notes[cell] = 0
        return True

    def toggle_note(self, cell: int, d: int) -> bool:
        if self.values[cell] or not 1 <= d <= 9:
            return False
        self._checkpoint()
        self._record(f"n{cell}={d}")
        self.notes[cell] ^= 1 << d
        return True

    def fill_notes(self) -> bool:
        """Pencil in every candidate of every empty cell."""
        new = [self.candidates(i) if not self.values[i] else 0 for i in range(81)]
        if new == self.notes:
            return False
        self._checkpoint()
        self._record("N")
        self.notes = new
        return True

    def restart(self) -> bool:
        if self.values == self.puzzle and not any(self.notes):
            return False
        self._checkpoint()
        self._record("R")
        self.values = list(self.puzzle)
        self.notes = [0] * 81
        return True

    # -- hints -----------------------------------------------------------
    def hint(self) -> Optional[Step]:
        """The next thing to do: first a wrong digit, else the easiest
        logical placement (with the steps that lead to it), else just the
        answer for a cell."""
        for i in range(81):
            if self.wrong(i):
                return Step(0, "mistake", cells=[i],
                            why=f"The {self.values[i]} in {cell_name(i)} is not right. Clear it first.")
        grid = list(self.values)
        logic = Logic(grid)
        # Respect the player's own notes where they already ruled things out.
        for i in range(81):
            if not grid[i] and self.notes[i]:
                narrowed = logic.cand[i] & self.notes[i]
                if narrowed & (1 << self.solution[i]):
                    logic.cand[i] = narrowed
        used: list[Step] = []
        while not logic.solved():
            step = logic.step(3)
            if step is None:
                break
            if step.place:
                if used:
                    names = ", ".join(dict.fromkeys(s.technique for s in used))
                    step = Step(max(s.level for s in used + [step]), step.technique, place=step.place,
                                cells=step.cells,
                                why=f"{used[0].why} Then: {step.why}"
                                if len(used) == 1 else f"After a few eliminations ({names}): {step.why}")
                return step
            used.append(step)
            logic.apply(step)
        empty = [i for i in range(81) if not self.values[i]]
        if not empty:
            return None
        cell = min(empty, key=lambda i: POP[self.candidates(i)])
        return Step(4, "reveal", place=(cell, self.solution[cell]), cells=[cell],
                    why=f"No simple step left here. The answer in {cell_name(cell)} is "
                        f"{self.solution[cell]}.")

    # -- (de)serialisation -----------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = self._state()
        data.update(
            seed=self.seed,
            difficulty=self.difficulty,
            puzzle=grid_text(self.puzzle),
            solution=grid_text(self.solution),
            elapsed=self.elapsed,
            started=self.started,
            mistakes=self.mistakes,
            hints=self.hints,
            history=self.history[-SAVED_UNDO_LIMIT:],
            log=self.log,
        )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sudoku":
        game = cls(int(data["seed"]), str(data.get("difficulty", "easy")),
                   puzzle=parse_grid(data["puzzle"]), solution=parse_grid(data["solution"]))
        game._load_state(data)
        game.elapsed = float(data.get("elapsed", 0.0))
        game.started = bool(data.get("started", False))
        game.mistakes = int(data.get("mistakes", 0))
        game.hints = int(data.get("hints", 0))
        game.history = list(data.get("history", []))
        game.log = [str(a) for a in data.get("log", [])]
        return game

    @classmethod
    def replay(cls, seed: int, difficulty: str, puzzle: str, solution: str, log: list[str],
               upto: Optional[int] = None) -> "Sudoku":
        """The game after the first `upto` logged actions (all by default)."""
        game = cls(seed, difficulty, puzzle=parse_grid(puzzle), solution=parse_grid(solution))
        for entry in log[:upto]:
            action, _, clock = entry.partition("@")
            game.elapsed = float(clock or 0)
            kind, rest = action[0], action[1:]
            if kind == "u":
                game.undo()
            elif kind == "N":
                game.fill_notes()
            elif kind == "R":
                game.restart()
            elif kind == "c":
                game.clear(int(rest))
            else:
                cell, d = (int(x) for x in rest.split("="))
                if kind == "n":
                    game.toggle_note(cell, d)
                else:
                    game.set_value(cell, d, hint=kind == "h")
        return game
