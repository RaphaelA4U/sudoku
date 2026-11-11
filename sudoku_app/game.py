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

DIFFICULTIES = ["beginner", "easy", "medium", "hard", "expert", "master"]
DIFFICULTY_INFO = {
    "beginner": "Lots of digits. Only 'where in this box can the 5 go'.",
    "easy": "Plenty of digits to start with. Looking around is enough.",
    "medium": "Fewer digits. Mostly looking around, sometimes pointing or a pair.",
    "hard": "Needs pairs and pointing: notes help a lot.",
    "expert": "Needs triples, X-wings and XY-wings. Notes are a must.",
    "master": "Needs skyscrapers, kites, XYZ-wings, unique rectangles and more. No chains.",
}

# Logic levels (see Logic.step): 1 hidden single in a box, 2 the other singles,
# 3 pointing and pairs, 4 triples, X-wing and XY-wing, 5 the master patterns.
# A difficulty needs at least `lo` and at most `hi`; easy, medium and hard give the
# same puzzles as before v1.4.0 (chat battles and puzzle numbers stay the same).
CLUE_FLOOR = {"beginner": 45, "easy": 36, "medium": 30, "hard": 0, "expert": 0, "master": 0}
LEVELS = {"beginner": (1, 1), "easy": (1, 2), "medium": (1, 3), "hard": (3, 3), "expert": (4, 4),
          "master": (5, 5)}
MAX_LEVEL = 5
# Part of the random seed per difficulty; fixed so new levels do not shift the old ones.
SALT = {"easy": 0, "medium": 1, "hard": 2, "expert": 3, "beginner": 4, "master": 5}

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
PEER_SETS = [set(p) for p in PEERS]


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
    level: int                        # see LEVELS: 1-2 singles, 3 pairs/pointing, 4 triples/wings, 5 master
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
    def step(self, max_level: int = MAX_LEVEL) -> Optional[Step]:
        finders = [self._hidden_single_box]
        if max_level >= 2:
            finders += [self._naked_single, self._hidden_single_line]
        if max_level >= 3:
            finders += [self._pointing, self._claiming, self._naked_pair, self._hidden_pair]
        if max_level >= 4:
            finders += [self._naked_triple, self._hidden_triple, self._x_wing, self._xy_wing]
        if max_level >= 5:
            finders += [self._skyscraper, self._kite, self._empty_rectangle, self._xyz_wing, self._w_wing,
                        self._unique_rectangle, self._naked_quad, self._hidden_quad, self._swordfish,
                        self._finned_x_wing, self._finned_swordfish, self._jellyfish, self._bug]
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
                    return Step(1 if kinds == ("box",) else 2, "hidden single", place=(spots[0], d), cells=unit,
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
                return Step(2, "naked single", place=(i, d), cells=[i],
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
                            return Step(3, "pointing", eliminate=elim, cells=spots,
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
                    return Step(3, "box/line", eliminate=elim, cells=spots,
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
        return self._naked_subset(2, 3, "naked pair")

    def _hidden_pair(self) -> Optional[Step]:
        return self._hidden_subset(2, 3, "hidden pair")

    def _naked_triple(self) -> Optional[Step]:
        return self._naked_subset(3, 4, "naked triple")

    def _hidden_triple(self) -> Optional[Step]:
        return self._hidden_subset(3, 4, "hidden triple")

    def _naked_quad(self) -> Optional[Step]:
        return self._naked_subset(4, 5, "naked quad")

    def _hidden_quad(self) -> Optional[Step]:
        return self._hidden_subset(4, 5, "hidden quad")

    def _fish(self, size: int, name: str, level: int) -> Optional[Step]:
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
                        return Step(level, name, eliminate=elim, cells=sorted(used),
                                    why=f"The {d}s of {base}s {bases} all sit in {cover}s {covers}, "
                                        f"so the rest of those {cover}s cannot have a {d}.")
        return None

    def _x_wing(self) -> Optional[Step]:
        return self._fish(2, "X-wing", 4)

    def _swordfish(self) -> Optional[Step]:
        return self._fish(3, "swordfish", 5)

    def _jellyfish(self) -> Optional[Step]:
        return self._fish(4, "jellyfish", 5)

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
                        return Step(4, "XY-wing", eliminate=elim, cells=[pivot, a, b],
                                    why=f"Whichever of {x} or {y} goes in {cell_name(pivot)}, one of the "
                                        f"two other marked cells becomes {z}, so cells that see both "
                                        f"cannot have a {z}.")
        return None

    # -- master patterns (level 5): fixed shapes, no chains ------------------
    def _sees(self, cell: int, *others: int) -> bool:
        return all(o != cell and o in PEER_SETS[cell] for o in others)

    def _cut(self, d: int, ends: list[int], skip: tuple[int, ...] = ()) -> list[tuple[int, int]]:
        """Remove d from every open cell that sees all of `ends`."""
        bit = 1 << d
        return [(i, d) for i in range(81) if i not in ends and i not in skip and self.cand[i] & bit
                and self._sees(i, *ends)]

    def _conjugates(self, d: int, kinds: tuple[str, ...]) -> list[tuple[str, int, list[int]]]:
        """Units where d fits in exactly two cells."""
        out = []
        for kind, idx, unit in UNITS:
            if kind in kinds:
                spots = self._places(unit, d)
                if len(spots) == 2:
                    out.append((kind, idx, spots))
        return out

    def _skyscraper(self) -> Optional[Step]:
        """Two lines where d fits twice, lined up at one end: one of the other ends is d."""
        for d in range(1, 10):
            for kind, other, key in (("row", "column", COL), ("column", "row", ROW)):
                links = self._conjugates(d, (kind,))
                for n, (_, i1, s1) in enumerate(links):
                    for _, i2, s2 in links[n + 1:]:
                        for a, b in ((0, 1), (1, 0)):
                            for c, e in ((0, 1), (1, 0)):
                                if key[s1[a]] != key[s2[c]] or key[s1[b]] == key[s2[e]]:
                                    continue
                                t1, t2 = s1[b], s2[e]
                                elim = self._cut(d, [t1, t2])
                                if elim:
                                    return Step(5, "skyscraper", eliminate=elim, cells=sorted(s1 + s2),
                                                why=f"In {kind}s {i1 + 1} and {i2 + 1} the {d} fits only twice, "
                                                    f"and one end of each is in {other} {key[s1[a]] + 1}. Only one "
                                                    f"of those two can be the {d}, so {cell_name(t1)} or "
                                                    f"{cell_name(t2)} is a {d}: cells that see both cannot be.")
        return None

    def _kite(self) -> Optional[Step]:
        """A row and a column where d fits twice, linked inside one box: one far end is d."""
        for d in range(1, 10):
            rows = self._conjugates(d, ("row",))
            cols = self._conjugates(d, ("column",))
            for _, ri, rs in rows:
                for _, ci, cs in cols:
                    if set(rs) & set(cs):
                        continue
                    for a, b in ((0, 1), (1, 0)):
                        for c, e in ((0, 1), (1, 0)):
                            if BOX[rs[a]] != BOX[cs[c]] or BOX[rs[b]] == BOX[rs[a]] or BOX[cs[e]] == BOX[cs[c]]:
                                continue
                            t1, t2 = rs[b], cs[e]
                            elim = self._cut(d, [t1, t2])
                            if elim:
                                return Step(5, "kite", eliminate=elim, cells=sorted(rs + cs),
                                            why=f"Row {ri + 1} and column {ci + 1} each have only two spots for "
                                                f"the {d}, and one of each sits in box {BOX[rs[a]] + 1}, where only "
                                                f"one can be the {d}. So {cell_name(t1)} or {cell_name(t2)} is "
                                                f"a {d}: cells that see both cannot be.")
        return None

    def _empty_rectangle(self) -> Optional[Step]:
        """In a box, d only fits on a cross (one row plus one column). With a line
        where d fits twice, the cross removes d where both would clash."""
        for d in range(1, 10):
            bit = 1 << d
            for b, box in enumerate(BOXES):
                spots = self._places(box, d)
                if len(spots) < 2:
                    continue
                for r in sorted({ROW[i] for i in box}):
                    for c in sorted({COL[i] for i in box}):
                        if any(ROW[s] != r and COL[s] != c for s in spots):
                            continue
                        if all(ROW[s] == r for s in spots) or all(COL[s] == c for s in spots):
                            continue
                        # a column (outside the box) where d fits twice, one end on row r
                        for kind, idx, (p, q) in self._conjugates(d, ("row", "column")):
                            for near, far in ((p, q), (q, p)):
                                if kind == "column":
                                    if COL[near] in {COL[i] for i in box} or ROW[near] != r \
                                            or ROW[far] in {ROW[i] for i in box}:
                                        continue
                                    target = ROW[far] * 9 + c
                                else:
                                    if ROW[near] in {ROW[i] for i in box} or COL[near] != c \
                                            or COL[far] in {COL[i] for i in box}:
                                        continue
                                    target = r * 9 + COL[far]
                                if self.grid[target] or not self.cand[target] & bit:
                                    continue
                                return Step(5, "empty rectangle", eliminate=[(target, d)],
                                            cells=sorted(spots + [p, q]),
                                            why=f"In box {b + 1} the {d} only fits on row {r + 1} or column "
                                                f"{c + 1}. In {kind} {idx + 1} it fits only in {cell_name(p)} and "
                                                f"{cell_name(q)}. A {d} in {cell_name(target)} would push the "
                                                f"{d} of that {kind} to {cell_name(near)}, and then box {b + 1} "
                                                f"has no place left for it.")
        return None

    def _xyz_wing(self) -> Optional[Step]:
        """Like an XY-wing, but the middle cell has three options: one of the three is z."""
        pairs = [i for i in range(81) if not self.grid[i] and POP[self.cand[i]] == 2]
        for pivot in range(81):
            if self.grid[pivot] or POP[self.cand[pivot]] != 3:
                continue
            pm = self.cand[pivot]
            wings = [i for i in pairs if i in PEER_SETS[pivot] and self.cand[i] & ~pm == 0]
            for n, a in enumerate(wings):
                for b in wings[n + 1:]:
                    if self.cand[a] | self.cand[b] != pm or POP[self.cand[a] & self.cand[b]] != 1:
                        continue
                    z = digits(self.cand[a] & self.cand[b])[0]
                    elim = self._cut(z, [pivot, a, b])
                    if elim:
                        x, y = (v for v in digits(pm) if v != z)
                        return Step(5, "XYZ-wing", eliminate=elim, cells=[pivot, a, b],
                                    why=f"{cell_name(pivot)} is {x}, {y} or {z}. Whichever it is, it or one "
                                        f"of the two other marked cells becomes {z}, so cells that see all "
                                        f"three cannot have a {z}.")
        return None

    def _w_wing(self) -> Optional[Step]:
        """Two cells with the same two options, linked by a line or box where one of
        those digits fits twice: one of the two cells is the other digit."""
        pairs = [i for i in range(81) if not self.grid[i] and POP[self.cand[i]] == 2]
        for n, p in enumerate(pairs):
            for q in pairs[n + 1:]:
                if self.cand[p] != self.cand[q] or q in PEER_SETS[p]:
                    continue
                for link, keep in (tuple(digits(self.cand[p])), tuple(reversed(digits(self.cand[p])))):
                    for kind, idx, (u, v) in self._conjugates(link, ("row", "column", "box")):
                        if {u, v} & {p, q}:
                            continue
                        if not ((self._sees(u, p) and self._sees(v, q)) or (self._sees(u, q) and self._sees(v, p))):
                            continue
                        elim = self._cut(keep, [p, q])
                        if elim:
                            return Step(5, "W-wing", eliminate=elim, cells=[p, q, u, v],
                                        why=f"{cell_name(p)} and {cell_name(q)} are both {keep} or {link}. In "
                                            f"{unit_name(kind, idx)} the {link} fits only twice, and each spot "
                                            f"sees one of them, so they cannot both be {link}: one is a {keep}, "
                                            f"and cells that see both cannot have a {keep}.")
        return None

    def _unique_rectangle(self) -> Optional[Step]:
        """Four cells in two rows, two columns and two boxes with the same two options
        would give the puzzle two solutions. Every puzzle here has one, so that pattern
        cannot happen (types 1, 2 and 4)."""
        for r1 in range(9):
            for r2 in range(r1 + 1, 9):
                for c1 in range(9):
                    for c2 in range(c1 + 1, 9):
                        if (r1 // 3 == r2 // 3) == (c1 // 3 == c2 // 3):
                            continue  # needs exactly two boxes
                        corners = [r1 * 9 + c1, r1 * 9 + c2, r2 * 9 + c1, r2 * 9 + c2]
                        if any(self.grid[i] for i in corners):
                            continue
                        common = self.cand[corners[0]] & self.cand[corners[1]] & self.cand[corners[2]] \
                            & self.cand[corners[3]]
                        for a, b in ((a, b) for a in digits(common) for b in digits(common) if a < b):
                            ab = (1 << a) | (1 << b)
                            floor = [i for i in corners if self.cand[i] == ab]
                            roof = [i for i in corners if i not in floor]
                            step = self._ur_step(a, b, ab, corners, floor, roof)
                            if step:
                                return step
        return None

    def _ur_step(self, a: int, b: int, ab: int, corners: list[int], floor: list[int],
                 roof: list[int]) -> Optional[Step]:
        intro = (f"{', '.join(cell_name(i) for i in corners[:3])} and {cell_name(corners[3])} all allow "
                 f"{a} and {b}. If they held only those, the {a}s and {b}s could swap and the puzzle would "
                 f"have two solutions.")
        if len(floor) == 3:
            elim = [(roof[0], d) for d in (a, b)]
            return Step(5, "unique rectangle", eliminate=elim, cells=corners,
                        why=f"{intro} So {cell_name(roof[0])} must be something else than {a} or {b}.")
        if len(floor) != 2 or roof[1] not in PEER_SETS[roof[0]]:
            return None
        extra0, extra1 = self.cand[roof[0]] & ~ab, self.cand[roof[1]] & ~ab
        if extra0 == extra1 and POP[extra0] == 1:
            c = digits(extra0)[0]
            elim = self._cut(c, roof)
            if elim:
                return Step(5, "unique rectangle", eliminate=elim, cells=corners,
                            why=f"{intro} So {cell_name(roof[0])} or {cell_name(roof[1])} is a {c}, and cells "
                                f"that see both cannot have a {c}.")
        for unit in (u for _, _, u in UNITS if roof[0] in u and roof[1] in u):
            for strong, weak in ((a, b), (b, a)):
                if self._places(unit, strong) == sorted(roof):
                    elim = [(i, weak) for i in roof]
                    return Step(5, "unique rectangle", eliminate=elim, cells=corners,
                                why=f"{intro} The {strong} must be in {cell_name(roof[0])} or "
                                    f"{cell_name(roof[1])}; a {weak} in the other one would make that pattern, "
                                    f"so neither can be a {weak}.")
        return None

    def _finned_fish(self, size: int, name: str) -> Optional[Step]:
        """A fish with extra spots (the fin) in one box: either the fish holds or the fin
        is d, and both remove d from the cells of the fish's lines inside that box."""
        from itertools import combinations

        for d in range(1, 10):
            for base, cover, key, other in (("row", "column", COL, COLS), ("column", "row", ROW, ROWS)):
                lines = ROWS if base == "row" else COLS
                candidates = [(li, self._places(line, d)) for li, line in enumerate(lines)]
                candidates = [(li, sp) for li, sp in candidates if 1 <= len(sp) <= size + 2]
                for combo in combinations(candidates, size):
                    used = [s for _, spots in combo for s in spots]
                    union = sorted({key[s] for s in used})
                    if len(union) <= size or len(union) > size + 2:
                        continue
                    for covered in combinations(union, size):
                        fins = [s for s in used if key[s] not in covered]
                        if len({BOX[f] for f in fins}) != 1:
                            continue
                        if any(not any(key[s] in covered for s in spots) for _, spots in combo):
                            continue
                        fb = BOX[fins[0]]
                        elim = [(i, d) for ci in covered for i in other[ci]
                                if BOX[i] == fb and i not in used and self.cand[i] >> d & 1]
                        if elim:
                            bases = ", ".join(str(li + 1) for li, _ in combo)
                            covers = ", ".join(str(c + 1) for c in covered)
                            return Step(5, name, eliminate=elim, cells=sorted(used),
                                        why=f"The {d}s of {base}s {bases} sit in {cover}s {covers}, apart from "
                                            f"{' and '.join(cell_name(f) for f in fins)} (the fin). Either "
                                            f"those {base}s form a {name.split()[-1]} or the fin is the {d}; "
                                            f"both rule out the {d} in box {fb + 1} on those {cover}s.")
        return None

    def _finned_x_wing(self) -> Optional[Step]:
        return self._finned_fish(2, "finned X-wing")

    def _finned_swordfish(self) -> Optional[Step]:
        return self._finned_fish(3, "finned swordfish")

    def _bug(self) -> Optional[Step]:
        """BUG+1: every open cell has two options except one with three. Two options
        everywhere would give two solutions, so that cell is the digit that fits three
        times in its row, column or box."""
        open_cells = [i for i in range(81) if not self.grid[i]]
        odd = [i for i in open_cells if POP[self.cand[i]] != 2]
        if len(odd) != 1 or POP[self.cand[odd[0]]] != 3:
            return None
        cell = odd[0]
        for d in digits(self.cand[cell]):
            # a real BUG: every digit fits 0 or 2 times in every unit, except d around this cell
            if all(len(self._places(u, x)) in ((0, 2, 3) if x == d and cell in u else (0, 2))
                   for _, _, u in UNITS for x in range(1, 10)) \
                    and all(len(self._places(u, d)) == 3 for _, _, u in UNITS if cell in u):
                elim = [(cell, x) for x in digits(self.cand[cell]) if x != d]
                return Step(5, "BUG+1", eliminate=elim, cells=[cell],
                            why=f"Every open cell has two options except {cell_name(cell)}. If it had two "
                                f"as well, the puzzle would have two solutions, so it is the {d}: the "
                                f"digit that fits three times in its row.")
        return None


def rate(puzzle: list[int]) -> int:
    """The hardest logic level needed (1 to MAX_LEVEL), or MAX_LEVEL + 1 when the
    patterns get stuck and only chains or guessing would go on."""
    logic = Logic(puzzle)
    worst = 1
    while not logic.solved():
        step = logic.step()
        if step is None:
            return MAX_LEVEL + 1
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


def battle_seed(battle: int) -> int:
    """The puzzle number for a chat battle: the same for every player."""
    return (int(battle) * 7919) % 999_999 + 1


def generate(seed: int, difficulty: str) -> tuple[list[int], list[int]]:
    """(puzzle, solution) for this number and difficulty; always the same pair."""
    rng = random.Random(seed * 10 + SALT[difficulty])
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
            if count_solutions(puzzle) != 1 or not _rate_at_most(puzzle, hi):
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
        self.battle: Optional[int] = None  # a chat battle id (beheer), when raced
        self.failed = False      # too many mistakes (the mistake limit was reached)
        # Notes that placing a digit removed, per cell: [the cell's own notes,
        # peers that lost that digit as a note]. Clearing the digit puts them back.
        self.removed: dict[int, list] = {}
        self.history: list[dict] = []
        # Every action in order, undos included: "v12=5" set ("v12=5!" = set
        # without removing that digit from other notes), "c12" clear,
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
        return {"values": grid_text(self.values), "notes": list(self.notes),
                "removed": {str(c): [own, list(peers)] for c, (own, peers) in self.removed.items()}}

    def _load_state(self, state: dict[str, Any]) -> None:
        self.values = parse_grid(state["values"])
        self.notes = [int(n) for n in state["notes"]]
        self.removed = {int(c): [int(v[0]), [int(p) for p in v[1]]]
                        for c, v in (state.get("removed") or {}).items()}

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
    def finished(self) -> bool:
        return self.is_solved() or self.failed

    def set_value(self, cell: int, d: int, hint: bool = False, clean_notes: bool = True) -> bool:
        if self.given(cell) or not 1 <= d <= 9 or self.values[cell] == d:
            return False
        self._checkpoint()
        self._record(f"{'h' if hint else 'v'}{cell}={d}{'' if clean_notes else '!'}")
        own = self.notes[cell] if not self.values[cell] else 0
        self.values[cell] = d
        self.notes[cell] = 0
        bit = 1 << d
        peers = []
        if clean_notes:  # the digit can no longer be a note in its row, column, box
            for p in PEERS[cell]:
                if self.notes[p] & bit:
                    self.notes[p] &= ~bit
                    peers.append(p)
        self.removed[cell] = [own, peers]
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
        d = self.values[cell]
        self.values[cell] = 0
        self.notes[cell] = 0
        own, peers = self.removed.pop(cell, [0, []])
        if d:
            # Put back what this digit took away: its own notes, and the notes
            # of that digit in its row, column and box (where it fits again).
            self.notes[cell] = own
            bit = 1 << d
            for p in peers:
                if not self.values[p] and not any(self.values[q] == d for q in PEERS[p]):
                    self.notes[p] |= bit
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
        self.removed = {}
        return True

    # -- hints -----------------------------------------------------------
    @staticmethod
    def _many_steps(used: list[Step], names: str, step: Step) -> str:
        """Several eliminations before the digit: explain the hardest one (from
        triples and wings up), the easy ones only by name."""
        key = max(used, key=lambda s: s.level)
        if key.level < 4:
            return f"After a few eliminations ({names}): {step.why}"
        return f"After a few eliminations ({names}). The key one, {key.technique}: {key.why} Then: {step.why}"

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
            step = logic.step()
            if step is None:
                break
            if step.place:
                if used:
                    names = ", ".join(dict.fromkeys(s.technique for s in used))
                    step = Step(max(s.level for s in used + [step]), step.technique, place=step.place,
                                cells=step.cells,
                                why=f"{used[0].why} Then: {step.why}"
                                if len(used) == 1 else self._many_steps(used, names, step))
                return step
            used.append(step)
            logic.apply(step)
        empty = [i for i in range(81) if not self.values[i]]
        if not empty:
            return None
        cell = min(empty, key=lambda i: POP[self.candidates(i)])
        return Step(MAX_LEVEL + 1, "reveal", place=(cell, self.solution[cell]), cells=[cell],
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
            battle=self.battle,
            failed=self.failed,
            # Race contract (BM Beheer chat battles): digits on the board.
            progress=self.filled(),
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
        game.battle = int(data["battle"]) if data.get("battle") else None
        game.failed = bool(data.get("failed", False))
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
                keep = rest.endswith("!")
                cell, d = (int(x) for x in rest.rstrip("!").split("="))
                if kind == "n":
                    game.toggle_note(cell, d)
                else:
                    game.set_value(cell, d, hint=kind == "h", clean_notes=not keep)
        return game
