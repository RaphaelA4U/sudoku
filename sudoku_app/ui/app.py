"""The Sudoku TUI, Claude-terminal style (same rules as the yahtzee and
solitaire apps).

Design rules:
- The terminal's own colors: default background and foreground everywhere,
  with purple as the selection/focus accent, orange for hints and cyan for
  "the same digit" and wins.
- Everything is text and ASCII. No buttons, no dialogs, no popups: the menu
  is arrow-navigable text, confirmations do not exist (games auto-save), and
  help/stats are full pages you enter and leave with escape.
- Arrow keys work everywhere; the mouse works everywhere: hover highlights,
  click a cell, click a digit below the grid to fill it in.
"""

from __future__ import annotations

import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Static

from .. import __version__
from ..config import (
    MISTAKE_INFO,
    MISTAKE_LABELS,
    MISTAKE_MODES,
    clear_saved_game,
    load_game_logs,
    load_game_snapshot,
    load_settings,
    record_game,
    save_game_snapshot,
    save_settings,
    stats_summary,
)
from ..game import BOX, COL, DIFFICULTIES, DIFFICULTY_INFO, ROW, Step, Sudoku, grid_text
from ..review import review_lines
from ..update import REPO_DIR, check_and_update

ACCENT = "#875fff"   # Claude-style purple, leaning slightly blue
HINT = "#ff9e3d"     # orange: hints (never yellow)
WINC = "#4dd0e1"     # cyan: the same digit, wins and good news

# figlet "DOS Rebel"
LOGO = """\
                        █████          █████
                       ░░███          ░░███
  █████  █████ ████  ███████   ██████  ░███ █████ █████ ████
 ███░░  ░░███ ░███  ███░░███  ███░░███ ░███░░███ ░░███ ░███
░░█████  ░███ ░███ ░███ ░███ ░███ ░███ ░██████░   ░███ ░███
 ░░░░███ ░███ ░███ ░███ ░███ ░███ ░███ ░███░░███  ░███ ░███
 ██████  ░░████████░░████████░░██████  ████ █████ ░░████████
░░░░░░    ░░░░░░░░  ░░░░░░░░  ░░░░░░  ░░░░ ░░░░░   ░░░░░░░░
"""

# Big digits, three cells wide and three rows high.
BIG = {
    1: ("╺┓ ", " ┃ ", "╺┻╸"),
    2: ("╺━┓", "┏━┛", "┗━╸"),
    3: ("╺━┓", " ━┫", "╺━┛"),
    4: ("╻ ╻", "┗━┫", "  ╹"),
    5: ("┏━╸", "┗━┓", "╺━┛"),
    6: ("┏━╸", "┣━┓", "┗━┛"),
    7: ("╺━┓", "  ┃", "  ╹"),
    8: ("┏━┓", "┣━┫", "┗━┛"),
    9: ("┏━┓", "┗━┫", "╺━┛"),
}

BIG_CELL = (7, 3)    # cell width, height: big digits and pencil notes
SMALL_CELL = (3, 1)  # compact, for small terminals
# Layouts, roomiest first: thin lines between all cells, only box lines,
# and compact. The board picks the first one that fits.
LAYOUTS = [("grid", BIG_CELL, True), ("open", BIG_CELL, False), ("small", SMALL_CELL, False)]
IDLE_PAUSE = 120.0   # the clock stops after this long without a key or mouse
SHIFTED = "!@#$%^&*("  # shift + 1..9 on a US keyboard: toggle a note

# Win fireworks: rockets climb, burst into digits, sparks fall and fade.
FX_DT = 0.05
FX_DURATION = 7.0
FX_GRAVITY = 14.0
FX_COLORS = [ACCENT, "red", WINC, HINT, "bold"]
FX_SYMBOLS = "123456789*+."


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Drawing: a tiny character canvas, converted to one rich Text per frame
# ---------------------------------------------------------------------------


class Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.w = max(1, width)
        self.h = max(1, height)
        self.chars = [[" "] * self.w for _ in range(self.h)]
        self.styles = [[""] * self.w for _ in range(self.h)]

    def put(self, x: int, y: int, text: str, style: str = "") -> None:
        if not 0 <= y < self.h:
            return
        row_c, row_s = self.chars[y], self.styles[y]
        for i, ch in enumerate(text):
            xx = x + i
            if 0 <= xx < self.w:
                row_c[xx] = ch
                row_s[xx] = style

    def to_text(self) -> Text:
        out = Text(no_wrap=True, overflow="crop")
        for y in range(self.h):
            buf, run = "", None
            for ch, st in zip(self.chars[y], self.styles[y]):
                if st != run:
                    if buf:
                        out.append(buf, style=run or "")
                    buf, run = "", st
                buf += ch
            if buf:
                out.append(buf.rstrip() if not run else buf, style=run or "")
            if y < self.h - 1:
                out.append("\n")
        return out


# ---------------------------------------------------------------------------
# Shared text widgets (same look and feel as the yahtzee and solitaire apps)
# ---------------------------------------------------------------------------

LABEL_W = 15  # menu label column


@dataclass
class MenuItem:
    id: str
    label: str
    kind: str = "action"  # action | choice
    choices: list = field(default_factory=list)
    index: int = 0
    visible: bool = True

    @property
    def value(self):
        return self.choices[self.index][1] if self.choices else None

    @property
    def value_label(self) -> str:
        return self.choices[self.index][0] if self.choices else ""


class AsciiMenu(Widget, can_focus=True):
    """A Claude-terminal style menu: text lines, arrows, hover, click."""

    BINDINGS = [
        Binding("up", "move(-1)", "Up", show=False),
        Binding("down", "move(1)", "Down", show=False),
        Binding("left", "adjust(-1)", "Left", show=False),
        Binding("right", "adjust(1)", "Right", show=False),
        Binding("space,enter", "activate", "Select", show=False),
    ]

    selected: reactive[int] = reactive(0)
    hovered: reactive[Optional[int]] = reactive(None)

    class Activated(Message):
        def __init__(self, item_id: str) -> None:
            self.item_id = item_id
            super().__init__()

    class Changed(Message):
        def __init__(self, item_id: str, value) -> None:
            self.item_id = item_id
            self.value = value
            super().__init__()

    class Highlighted(Message):
        def __init__(self, item_id: str) -> None:
            self.item_id = item_id
            super().__init__()

    def __init__(self, items: list[MenuItem], id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self.items = items

    def visible_items(self) -> list[MenuItem]:
        return [i for i in self.items if i.visible]

    def get_content_width(self, container, viewport) -> int:
        width = 10
        for item in self.items:
            if item.kind == "choice":
                widest = max((len(lbl) for lbl, _ in item.choices), default=0)
                width = max(width, 2 + LABEL_W + 2 + widest + 2)
            else:
                width = max(width, 2 + len(item.label))
        return width + 1

    def get_content_height(self, container, viewport, width) -> int:
        return len(self.visible_items())

    def item(self, item_id: str) -> MenuItem:
        return next(i for i in self.items if i.id == item_id)

    def set_visible(self, item_id: str, visible: bool) -> None:
        self.item(item_id).visible = visible
        vis = self.visible_items()
        self.selected = max(0, min(self.selected, len(vis) - 1))
        self.refresh(layout=True)

    def current_item(self) -> Optional[MenuItem]:
        vis = self.visible_items()
        return vis[self.selected] if vis else None

    def render(self) -> Text:
        out = Text(no_wrap=True)
        for idx, item in enumerate(self.visible_items()):
            selected = idx == self.selected and self.has_focus
            hovered = idx == self.hovered
            style = f"bold {ACCENT}" if selected else ("bold" if hovered else "")
            out.append("> " if selected else "  ", style=f"bold {ACCENT}" if selected else "")
            if item.kind == "choice":
                out.append(f"{item.label:<{LABEL_W}}", style=style)
                out.append("< ", style="dim")
                out.append(item.value_label, style=style if selected or hovered else "")
                out.append(" >", style="dim")
            else:
                out.append(item.label, style=style)
            out.append("\n")
        return out

    def action_move(self, step: int) -> None:
        vis = self.visible_items()
        if vis:
            self.selected = (self.selected + step) % len(vis)

    def action_adjust(self, step: int) -> None:
        item = self.current_item()
        if item and item.kind == "choice" and item.choices:
            item.index = (item.index + step) % len(item.choices)
            self.refresh()
            self.post_message(self.Changed(item.id, item.value))

    def action_activate(self) -> None:
        item = self.current_item()
        if item is None:
            return
        if item.kind == "choice":
            self.action_adjust(1)
        else:
            self.post_message(self.Activated(item.id))

    def on_click(self, event: events.Click) -> None:
        vis = self.visible_items()
        if 0 <= event.y < len(vis):
            self.focus()
            self.selected = event.y
            self.action_activate()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        vis = self.visible_items()
        self.hovered = event.y if 0 <= event.y < len(vis) else None

    def on_leave(self) -> None:
        self.hovered = None

    def watch_selected(self, _) -> None:
        self.refresh()
        item = self.current_item()
        if item:
            self.post_message(self.Highlighted(item.id))

    def watch_hovered(self, _) -> None:
        self.refresh()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()


class ActionBar(Static):
    """Footer key hints; the ones bound to an action are clickable."""

    hovered_idx: reactive[Optional[int]] = reactive(None)

    class Invoked(Message):
        def __init__(self, action: str) -> None:
            self.action = action
            super().__init__()

    def __init__(self, segments: list, id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self.segments = segments
        self._ranges: list[tuple[int, int, int]] = []

    def set_segments(self, segments: list) -> None:
        self.segments = segments
        self.hovered_idx = None
        self.refresh()

    def render(self) -> Text:
        out = Text(" ", no_wrap=True)
        self._ranges = []
        for idx, (label, action) in enumerate(self.segments):
            start = len(out.plain)
            if action is None:
                out.append(label, style="dim")
            elif idx == self.hovered_idx:
                out.append(label, style=f"bold underline {ACCENT}")
            else:
                out.append(label)
            self._ranges.append((start, len(out.plain), idx))
            out.append("   ")
        return out

    def _idx_at(self, x: int) -> Optional[int]:
        for start, end, idx in self._ranges:
            if start <= x < end and self.segments[idx][1] is not None:
                return idx
        return None

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.hovered_idx = self._idx_at(event.x)

    def on_leave(self) -> None:
        self.hovered_idx = None

    def on_click(self, event: events.Click) -> None:
        idx = self._idx_at(event.x)
        if idx is not None:
            self.post_message(self.Invoked(self.segments[idx][1]))

    def watch_hovered_idx(self, _) -> None:
        self.refresh()


class TextPage(Screen):
    """A scrollable full-screen text page. Escape (or q) goes back."""

    BINDINGS = [Binding("escape,q,backspace", "go_back", "Back")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.title_text = title
        self.body = body

    def compose(self) -> ComposeResult:
        yield Static(f" {self.title_text}", id="page-title", markup=True)
        with VerticalScroll(id="page-scroll"):
            yield Static(self.body, id="page-body", markup=True)
        yield ActionBar([("up/down scroll", None), ("esc back", "back")], id="page-footer")

    def on_mount(self) -> None:
        self.query_one("#page-scroll", VerticalScroll).focus()

    def on_action_bar_invoked(self, event: ActionBar.Invoked) -> None:
        if event.action == "back":
            self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()


def review_page(entry: dict) -> TextPage:
    lines = review_lines(int(entry["seed"]), str(entry.get("difficulty", "easy")), str(entry["puzzle"]),
                         str(entry["solution"]), list(entry.get("log") or []), bool(entry.get("won")))
    return TextPage("Review", "\n".join(lines))


HELP_TEXT = f"""[b]SUDOKU v{__version__}[/b]

[b u]Goal[/b u]
  Fill the grid so that every row, every column and every 3x3 box holds
  the digits 1 to 9 exactly once. Every puzzle has exactly one solution.

[b u]Keys[/b u]
  [b]arrows[/b]         move around the grid
  [b]1-9[/b]            fill in a digit (the same digit again clears it; another
                 digit needs the cell cleared first, so a misclick does nothing)
  [b]n[/b]              notes mode on/off: 1-9 then pencil in small notes
  [b]shift+1-9[/b]      pencil a note right away, in either mode
  [b]0 backspace x[/b]  clear the cell
  [b]f[/b]              fill in every possible note at once
  [b]h[/b]              hint: shows the next step and why; [b]h[/b] again fills it in
  [b]u ctrl+z[/b]       undo (as often as you like)
  [b]r[/b]              start this puzzle over (undo brings it back)
  [b]v[/b]              review: every move so far, what was forced and what was a guess
  [b]esc[/b]            back to the menu (the game is saved)
  [b]?[/b]              this help
  [b]ctrl+q[/b]         quit (the game is saved)

[b u]Mouse[/b u]
  Click a cell to select it, then click a digit below the grid to fill it
  in. "notes" below the grid switches notes mode. Every key in the footer
  can be clicked too.

[b u]Colors[/b u]
  Digits you start with are plain, the ones you fill in are purple. The
  digit you are on lights up cyan everywhere, notes included. A digit that
  is done (all nine placed) fades in the row below the grid.

[b u]Options[/b u] [dim](main menu)[/dim]
  [b]Difficulty[/b]     easy, medium, hard or expert. Easy and medium only need
                 looking around; hard needs pairs and pointing; expert
                 needs X-wings, triples and XY-wings.
  [b]Mistakes[/b]       "show wrong digits" turns a wrong digit red right away,
                 "show conflicts" only when it clashes with its row, column
                 or box, "hidden" never.
  [b]Mistake limit[/b]  "3 mistakes" (default): the third wrong digit ends the game, as
                 on sudoku.com; [b]r[/b] then tries the same puzzle again. The count
                 shows top right. "off": no limit.
  [b]Auto notes[/b]     on (default): placing a digit removes it from the notes in its
                 row, column and box. Clearing the digit puts them back.
  [b]Highlight[/b]      on (default): the row, column and box of the selected cell
                 light up (their empty spots and notes turn purple).
  [b]Win animation[/b]  fireworks when you solve it; any key or click skips them.

[b u]Hints[/b u]
  A hint first points out a wrong digit, if there is one. Otherwise it
  shows the easiest next step and explains it, the way a person would
  solve it. Press [b]h[/b] again to fill it in. Hints count in your statistics.

[b u]Puzzles[/b u]
  Every puzzle has a number (top left). `sudoku --puzzle 123 --difficulty
  hard` plays exactly that puzzle again, handy to challenge a friend.

[b u]Statistics[/b u]
  A puzzle counts once you have filled in a digit: solved, or not solved
  when you start another one instead. Quitting halfway is fine: the game
  is saved and "Continue" picks it up again. Every move of every puzzle is
  kept in ~/.config/sudoku/games.jsonl, so games can be reviewed later.

[b u]Clock[/b u]
  The clock only runs while you play: it pauses when the terminal is in the
  background, and after two minutes without a key or a click.

[b u]Updates[/b u]
  Like Claude Code, the game updates itself on start when a new version
  is out. `sudoku --no-update` skips that once.
"""


def changelog_text() -> str:
    path = Path(REPO_DIR) / "CHANGELOG.md"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return "No changelog found."
    out = []
    for line in lines:
        if line.startswith("# "):
            continue
        out.append(f"[b]{line[3:]}[/b]" if line.startswith("## ") else line)
    return "\n".join(out).strip()


def _changelog_summary(version: str, max_lines: int = 6) -> Optional[str]:
    path = Path(REPO_DIR) / "CHANGELOG.md"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    out: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            in_section = version in line
            continue
        if in_section and line.strip():
            out.append(line.strip().lstrip("- "))
            if len(out) >= max_lines:
                break
    return "\n".join(out) if out else None


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------


class Board(Widget, can_focus=True):
    """Draws the grid and the digit row below it, and turns mouse input
    into cell and digit clicks."""

    class CellClicked(Message):
        def __init__(self, cell: int) -> None:
            self.cell = cell
            super().__init__()

    class DigitClicked(Message):
        def __init__(self, digit: int) -> None:
            self.digit = digit
            super().__init__()

    class NotesClicked(Message):
        pass

    class SkipFx(Message):
        """A click during the win fireworks: skip them."""

    def __init__(self, game: Sudoku, id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self.game = game
        self.cursor = 40  # the middle cell
        self.notes_mode = False
        self.hint: Optional[Step] = None
        self.hover: Optional[tuple[str, int]] = None  # ("cell"|"digit"|"notes", n)
        self.mistake_mode = "off"
        self.highlight = True
        self.cw, self.ch = BIG_CELL
        self.thin = True  # thin lines between cells inside a box
        self._rects: list[tuple[int, int, int, int, str, int]] = []
        self.banner: Optional[tuple[str, str]] = None
        self.particles: list[list] = []
        self._fx_time = 0.0
        self._fx_next_rocket = 0.0
        self._fx_rng = random.Random()

    # -- geometry --------------------------------------------------------
    def grid_size(self, cw: int, ch: int, thin: bool) -> tuple[int, int]:
        if thin:
            return 9 * cw + 10, 9 * ch + 10
        return 9 * cw + 4, 9 * ch + 4

    def pick_size(self) -> None:
        for _, (cw, ch), thin in LAYOUTS:
            w, h = self.grid_size(cw, ch, thin)
            if self.size.width >= w and self.size.height >= h + 2:
                break
        self.cw, self.ch, self.thin = cw, ch, thin

    def origin(self) -> tuple[int, int]:
        w, h = self.grid_size(self.cw, self.ch, self.thin)
        x0 = max(0, (self.size.width - w) // 2)
        y0 = max(0, (self.size.height - h - 2) // 2)
        return x0, y0

    def _line_offset(self, k: int, size: int) -> int:
        """Offset of grid line k (0..9) from the grid's edge."""
        if self.thin:
            return k * (size + 1)
        return k * size + k // 3 if k % 3 else (k // 3) * (3 * size + 1)

    def cell_xy(self, cell: int) -> tuple[int, int]:
        x0, y0 = self.origin()
        r, c = ROW[cell], COL[cell]
        if self.thin:
            return x0 + 1 + c * (self.cw + 1), y0 + 1 + r * (self.ch + 1)
        return x0 + 1 + c * self.cw + c // 3, y0 + 1 + r * self.ch + r // 3

    # -- win fireworks ---------------------------------------------------
    def start_fireworks(self, detail: str) -> None:
        self.banner = ("S O L V E D !", detail)
        self.particles = []
        self._fx_time = 0.0
        self._fx_next_rocket = 0.0
        self.refresh()

    def stop_fireworks(self) -> None:
        self.banner = None
        self.particles = []
        self.refresh()

    def step_fireworks(self) -> bool:
        """Advance one frame; False when the show is over."""
        rng = self._fx_rng
        w, h = max(self.size.width, 20), max(self.size.height, 10)
        self._fx_time += FX_DT
        if self._fx_time < FX_DURATION - 1.5 and self._fx_time >= self._fx_next_rocket:
            self._fx_next_rocket = self._fx_time + rng.uniform(0.15, 0.45)
            climb = rng.uniform(0.35, 0.8) * h
            vy = -(2 * FX_GRAVITY * climb) ** 0.5
            self.particles.append([rng.uniform(4, w - 4), h - 1, rng.uniform(-3, 3), vy,
                                   "|", "bold", 9.0, True])
        alive = []
        for p in self.particles:
            p[0] += p[2] * FX_DT
            p[1] += p[3] * FX_DT
            p[3] += FX_GRAVITY * FX_DT
            p[6] -= FX_DT
            if p[7] and p[3] >= -1.0:
                color = rng.choice(FX_COLORS)
                for _ in range(rng.randint(14, 22)):
                    angle = rng.uniform(0, 6.2832)
                    speed = rng.uniform(5, 12)
                    alive.append([p[0], p[1], math.cos(angle) * speed * 2, math.sin(angle) * speed - 3,
                                  rng.choice(FX_SYMBOLS), color, rng.uniform(1.0, 2.0), False])
                continue
            if p[6] > 0 and -2 < p[0] < w + 2 and p[1] < h + 1:
                alive.append(p)
        self.particles = alive
        self.refresh()
        return self._fx_time < FX_DURATION or bool(self.particles) and self._fx_time < FX_DURATION + 2

    def _draw_fireworks(self, cv: Canvas) -> None:
        for x, y, _vx, _vy, ch, style, life, rocket in self.particles:
            if rocket:
                cv.put(int(x), int(y), ch, style)
                cv.put(int(x), int(y) + 1, "'", "dim")
            else:
                cv.put(int(x), int(y), ch, style if life > 0.45 else f"dim {style}")
        if self.banner:
            title, detail = self.banner
            inner = max(len(title), len(detail)) + 8
            x = max(0, (cv.w - inner - 2) // 2)
            y = max(0, (cv.h - 6) // 2)
            edge = "+" + "-" * inner + "+"
            cv.put(x, y, edge, f"bold {WINC}")
            for r, (text, style) in enumerate([("", ""), (title, f"bold {WINC}"),
                                                (detail, "bold"), ("", "")], start=1):
                cv.put(x + 1, y + r, " " * inner, "")
                cv.put(x, y + r, "|", f"bold {WINC}")
                cv.put(x + 1, y + r, f"{text:^{inner}}", style)
                cv.put(x + inner + 1, y + r, "|", f"bold {WINC}")
            cv.put(x, y + 5, edge, f"bold {WINC}")

    # -- rendering -------------------------------------------------------
    def _bad_cells(self) -> set[int]:
        g = self.game
        if self.mistake_mode == "wrong":
            return {i for i in range(81) if g.wrong(i)}
        if self.mistake_mode == "conflicts":
            return g.conflicts() - {i for i in range(81) if g.given(i)}
        return set()

    def render(self) -> Text:
        g = self.game
        self.pick_size()
        cw, ch = self.cw, self.ch
        cv = Canvas(self.size.width, self.size.height)
        rects: list[tuple[int, int, int, int, str, int]] = []
        x0, y0 = self.origin()
        gw, gh = self.grid_size(cw, ch, self.thin)

        # Grid lines: thin ones (dim) between cells, box lines on top.
        border = "dim" if not self.has_focus else ""
        if self.thin:
            for k in (1, 2, 4, 5, 7, 8):
                y = y0 + k * (ch + 1)
                cv.put(x0 + 1, y, "-" * (gw - 2), "dim")
                x = x0 + k * (cw + 1)
                for yy in range(y0 + 1, y0 + gh - 1):
                    cv.put(x, yy, "|" if (yy - y0) % (ch + 1) else "+", "dim")
        for k in (0, 3, 6, 9):
            y = y0 + self._line_offset(k, ch)
            cv.put(x0, y, "-" * gw, border)
            x = x0 + self._line_offset(k, cw)
            for yy in range(y0, y0 + gh):
                cv.put(x, yy, "|", border)
        for kx in (0, 3, 6, 9):
            for ky in (0, 3, 6, 9):
                cv.put(x0 + self._line_offset(kx, cw), y0 + self._line_offset(ky, ch), "+", border)

        cur = self.cursor
        cur_digit = g.values[cur]
        lit = set()
        if self.highlight and self.banner is None:
            lit = {i for i in range(81) if i != cur and (ROW[i] == ROW[cur] or COL[i] == COL[cur]
                                                         or BOX[i] == BOX[cur])}
        bad = self._bad_cells()
        hint_cell = self.hint.place[0] if self.hint and self.hint.place else (
            self.hint.cells[0] if self.hint and self.hint.technique == "mistake" else None)
        proof = set(self.hint.cells) if self.hint else set()

        for cell in range(81):
            x, y = self.cell_xy(cell)
            v = g.values[cell]
            given = g.given(cell)
            selected = cell == cur and self.banner is None
            hovered = self.hover == ("cell", cell)
            if v:
                if cell in bad:
                    color = "red"
                elif v == cur_digit and not selected:
                    color = WINC
                elif given:
                    color = ""
                else:
                    color = ACCENT
                style = f"bold {color}".strip()
            else:
                style = ""
            back = ""
            if cell == hint_cell:
                back = f"reverse {HINT}"
            elif selected and self.has_focus:
                back = f"reverse {'red' if cell in bad else ACCENT}"
            elif selected:
                back = "reverse"
            if back:
                cv.put(x, y, " " * cw, back)
                for r in range(1, ch):
                    cv.put(x, y + r, " " * cw, back)
                style = f"bold {back}"
            elif hovered:
                # Small corner marks around the cell under the mouse (an
                # underline would cut through the big digits).
                mark = f"bold {ACCENT}"
                if ch >= 3:
                    cv.put(x, y, "┌", mark)
                    cv.put(x + cw - 1, y, "┐", mark)
                    cv.put(x, y + ch - 1, "└", mark)
                    cv.put(x + cw - 1, y + ch - 1, "┘", mark)
                else:
                    cv.put(x, y, "[", mark)
                    cv.put(x + cw - 1, y, "]", mark)
            # Contents.
            if v:
                if ch >= 3:
                    for r, line in enumerate(BIG[v]):
                        for i, chh in enumerate(line):
                            if chh != " ":
                                cv.put(x + 2 + i, y + r, chh, style)
                else:
                    cv.put(x + 1, y, str(v), style)
            elif g.notes[cell] and ch >= 3:
                for d in range(1, 10):
                    if g.notes[cell] >> d & 1:
                        nstyle = back or (f"bold {WINC}" if d == cur_digit else (ACCENT if cell in lit else "dim"))
                        cv.put(x + 1 + 2 * ((d - 1) % 3), y + (d - 1) // 3, str(d), nstyle)
            else:
                mark = "∙" if g.notes[cell] else "·"
                in_proof = cell in proof and self.hint is not None
                cv.put(x + cw // 2, y + ch // 2, mark,
                       back or (HINT if in_proof else ("bold" if hovered else (f"bold {ACCENT}" if cell in lit else "dim"))))
            rects.append((x, y, x + cw, y + ch, "cell", cell))

        # The digit row: click to fill in; done digits fade.
        py = y0 + gh + 1
        step = 4 if self.ch >= 3 else 3
        px = x0 + max(0, (gw - (9 * step + 10)) // 2)
        for d in range(1, 10):
            done = g.count(d) >= 9
            if self.hover == ("digit", d):
                st = f"bold underline {ACCENT}"
            elif d == cur_digit:
                st = f"bold {WINC}"
            elif done:
                st = "dim"
            else:
                st = "bold"
            cv.put(px, py, f" {d} ", st)
            rects.append((px, py, px + 3, py + 1, "digit", d))
            px += step
        label = f"notes {'on' if self.notes_mode else 'off'}"
        st = f"bold underline {ACCENT}" if self.hover == ("notes", 0) else (
            f"bold {ACCENT}" if self.notes_mode else "dim")
        cv.put(px + 1, py, label, st)
        rects.append((px + 1, py, px + 1 + len(label), py + 1, "notes", 0))
        self._rects = rects
        if self.banner or self.particles:
            self._draw_fireworks(cv)
        return cv.to_text()

    # -- mouse -----------------------------------------------------------
    def hit(self, x: int, y: int) -> Optional[tuple[str, int]]:
        for x0, y0, x1, y1, kind, n in self._rects:
            if x0 <= x < x1 and y0 <= y < y1:
                return kind, n
        return None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.focus()
        if self.banner is not None:
            self.post_message(self.SkipFx())
            return
        target = self.hit(event.x, event.y)
        if target is None:
            return
        kind, n = target
        if kind == "cell":
            self.post_message(self.CellClicked(n))
        elif kind == "digit":
            self.post_message(self.DigitClicked(n))
        else:
            self.post_message(self.NotesClicked())

    def on_mouse_move(self, event: events.MouseMove) -> None:
        target = self.hit(event.x, event.y)
        if target != self.hover:
            self.hover = target
            self.refresh()

    def on_leave(self) -> None:
        if self.hover is not None:
            self.hover = None
            self.refresh()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()


FOOTER = [
    ("arrows move", None),
    ("1-9 fill in", None),
    ("n notes", "notes"),
    ("h hint", "hint"),
    ("u undo", "undo"),
    ("? help", "help"),
    ("esc menu", "menu"),
]

FOOTER_OVER = [
    ("r try again", "retry"),
    ("enter new puzzle", "new"),
    ("v review", "review"),
    ("m menu", "menu"),
]

FOOTER_WON = [
    ("enter new puzzle", "new"),
    ("v review", "review"),
    ("m menu", "menu"),
    ("ctrl+q quit", "quit"),
]


class GameScreen(Screen):
    BINDINGS = [
        Binding("left", "cursor(-1, 0)", show=False),
        Binding("right", "cursor(1, 0)", show=False),
        Binding("up", "cursor(0, -1)", show=False),
        Binding("down", "cursor(0, 1)", show=False),
        Binding("n", "notes", show=False),
        Binding("f", "fill_notes", show=False),
        Binding("h", "hint", show=False),
        Binding("u,ctrl+z", "undo", show=False),
        Binding("r", "restart", show=False),
        Binding("0,backspace,delete,x", "clear", show=False),
        Binding("enter", "enter", show=False),
        Binding("m", "menu", show=False),
        Binding("v", "review", show=False),
        Binding("escape", "escape", show=False),
        Binding("question_mark,f1", "help", show=False),
        Binding("ctrl+q", "quit_app", show=False),
    ]

    def __init__(self, game: Sudoku) -> None:
        super().__init__()
        self.game = game
        self.recorded = False
        self._fx_timer = None
        self._note_text = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="statusbar")
        yield Board(self.game, id="board")
        yield Static("", id="note", markup=True)
        yield ActionBar(FOOTER, id="footer-keys")

    @property
    def board(self) -> Board:
        return self.query_one(Board)

    def _load_prefs(self) -> None:
        settings = load_settings()
        self.board.mistake_mode = str(settings.get("mistakes", "off"))
        self.board.highlight = bool(settings.get("highlight", True))
        self.auto_notes = bool(settings.get("auto_notes", True))
        self.limit = int(settings.get("mistake_limit", 3) or 0)

    def on_mount(self) -> None:
        board = self.board
        board.focus()
        self._load_prefs()
        # Start on the first empty cell.
        board.cursor = next((i for i in range(81) if not self.game.values[i]), 40)
        self.set_interval(1.0, self._tick)
        g = self.game
        if g.is_solved():
            self._won()
        elif g.failed:
            self._failed()
        elif g.started:
            self.note(f"Welcome back. Puzzle #{g.seed}, {fmt_time(g.elapsed)} on the clock.")
        else:
            self.note(f"Puzzle #{g.seed}, {g.difficulty}. Arrows and 1-9 to fill in, "
                      f"[b]n[/b] for notes, [b]h[/b] for a hint.")
        self._refresh_all()

    def on_screen_resume(self) -> None:
        self._load_prefs()
        self._refresh_all()

    # -- status ----------------------------------------------------------
    def note(self, text: str) -> None:
        self._note_text = text
        try:
            self.query_one("#note", Static).update(f" {text}")
        except Exception:  # noqa: BLE001 - screen tearing down
            pass

    def _status_text(self) -> Text:
        g = self.game
        left = Text(" ")
        left.append("SUDOKU", style=f"bold {ACCENT}")
        left.append(f"  Puzzle #{g.seed}  {g.difficulty.capitalize()}", style="dim")
        if g.battle:
            left.append("  Battle", style=f"bold {WINC}")
        right = Text()
        if self.board.mistake_mode != "off" or self.limit:
            right.append("Mistakes ", style="dim")
            right.append(str(g.mistakes), style="bold red" if g.mistakes else "bold")
            if self.limit:
                right.append(f"/{self.limit}", style="dim")
            right.append("   ")
        right.append("Hints ", style="dim")
        right.append(str(g.hints), style="bold")
        if load_settings().get("timer", True):
            right.append("   ")
            if g.started and not g.finished() and not self.app.clock_running():
                right.append("paused ", style="dim")
            right.append(fmt_time(g.elapsed), style="bold")
        right.append(" ")
        width = self.size.width or 80
        pad = max(1, width - left.cell_len - right.cell_len)
        return left + Text(" " * pad) + right

    def _refresh_all(self) -> None:
        try:
            self.query_one("#statusbar", Static).update(self._status_text())
        except Exception:  # noqa: BLE001
            return
        self.board.refresh()

    def on_resize(self) -> None:
        self._refresh_all()

    def _tick(self) -> None:
        g = self.game
        if not g.started or g.finished() or self.app.screen is not self:
            return
        if self.app.clock_running():
            g.elapsed += 1
            if int(g.elapsed) % 15 == 0:
                self._save()
        self.query_one("#statusbar", Static).update(self._status_text())

    # -- bookkeeping after every action ----------------------------------
    def _save(self) -> None:
        if self.game.finished():
            clear_saved_game()
            return
        save_game_snapshot({"version": __version__, "game": self.game.to_dict()})

    def _after_action(self, keep_hint: bool = False) -> None:
        if not keep_hint:
            self.board.hint = None
        g = self.game
        if not g.is_solved() and not g.failed and self.limit and g.mistakes >= self.limit:
            g.failed = True
        self._save()
        self._refresh_all()
        if g.is_solved():
            self._won()
        elif g.failed:
            self._failed()

    def _stats_entry(self, won: bool) -> dict:
        g = self.game
        return {
            "won": won,
            "difficulty": g.difficulty,
            "time": int(g.elapsed),
            "mistakes": g.mistakes,
            "hints": g.hints,
            "seed": g.seed,
            **({"battle": g.battle} if g.battle else {}),
        }

    def _won(self) -> None:
        g = self.game
        if not self.recorded and g.started:
            self.recorded = True
            record_game(self._stats_entry(won=True), game=g.to_dict())
            clear_saved_game()
        parts = [fmt_time(g.elapsed)]
        if self.board.mistake_mode != "off" or g.mistakes:
            parts.append(f"{g.mistakes} mistake{'s' if g.mistakes != 1 else ''}")
        parts.append(f"{g.hints} hint{'s' if g.hints != 1 else ''}")
        self.note(f"[bold {WINC}]Solved![/] {', '.join(parts)}.")
        self.query_one("#footer-keys", ActionBar).set_segments(FOOTER_WON)
        self._refresh_all()
        if load_settings().get("win_animation", True) and self._fx_timer is None and g.started:
            self.board.start_fireworks("   ".join(parts))
            self._fx_timer = self.set_interval(FX_DT, self._fx_step)

    def _failed(self) -> None:
        """The mistake limit was reached: game over (like sudoku.com)."""
        g = self.game
        if not self.recorded and g.started:
            self.recorded = True
            record_game(dict(self._stats_entry(won=False), reason="mistakes"), game=g.to_dict())
            clear_saved_game()
        self.board.hint = None
        self.note(f"[bold red]Game over:[/] {g.mistakes} mistakes. [b]r[/b] try this puzzle again, "
                  f"[b]enter[/b] a new one, [b]v[/b] review. [dim](Mistake limit in the menu.)[/dim]")
        self.query_one("#footer-keys", ActionBar).set_segments(FOOTER_OVER)
        self._refresh_all()

    def _fx_step(self) -> None:
        if not self.board.step_fireworks():
            self.stop_fireworks()

    def stop_fireworks(self) -> None:
        if self._fx_timer is not None:
            self._fx_timer.stop()
        self._fx_timer = None
        self.board.stop_fireworks()

    def abandon(self) -> None:
        """Count the current puzzle as not solved (when it was started)."""
        g = self.game
        if self.recorded or not g.started or g.finished():
            return
        self.recorded = True
        record_game(self._stats_entry(won=False), game=g.to_dict())
        clear_saved_game()

    # -- input -----------------------------------------------------------
    def _editable(self) -> bool:
        return not self.game.finished() and not self.game.given(self.board.cursor)

    def _enter_digit(self, d: int, note: bool = False) -> None:
        g, cell = self.game, self.board.cursor
        if g.finished():
            return
        if g.given(cell):
            self.note("That digit was given; it cannot change.")
            return
        if note or self.board.notes_mode:
            if g.values[cell]:
                self.note("Clear the digit first to pencil notes here.")
                return
            g.toggle_note(cell, d)
            self._after_action()
            return
        if g.values[cell] == d:
            g.clear(cell)
        elif g.values[cell]:
            # No overwriting: a misclick on the digit row must not replace a
            # digit (and count as a mistake). Clear the cell first.
            self.note(f"There is a {g.values[cell]} here. Clear it first ([b]backspace[/b] or [b]0[/b]).")
            return
        else:
            g.set_value(cell, d, clean_notes=self.auto_notes)
            if self.board.mistake_mode == "wrong" and g.wrong(cell):
                self.note(f"[red]The {d} does not belong there.[/]")
            elif g.count(d) == 9 and not g.is_solved():
                self.note(f"All nine {d}s are in.")
            else:
                self.note("")
        self._after_action()

    async def on_key(self, event: events.Key) -> None:
        if self._fx_timer is not None:
            # Any key skips the celebration (and does nothing else).
            event.stop()
            event.prevent_default()
            self.stop_fireworks()
            return
        ch = event.character
        if ch and len(ch) == 1 and (ch in "123456789" or ch in SHIFTED):
            event.stop()
            event.prevent_default()
            if ch in SHIFTED:
                self._enter_digit(SHIFTED.index(ch) + 1, note=True)
            else:
                self._enter_digit(int(ch))

    @on(Board.CellClicked)
    def _cell_clicked(self, event: Board.CellClicked) -> None:
        self.board.cursor = event.cell
        self._refresh_all()

    @on(Board.DigitClicked)
    def _digit_clicked(self, event: Board.DigitClicked) -> None:
        self._enter_digit(event.digit)

    @on(Board.NotesClicked)
    def _notes_clicked(self, _: Board.NotesClicked) -> None:
        self.action_notes()

    @on(Board.SkipFx)
    def _skip_fx(self, _: Board.SkipFx) -> None:
        self.stop_fireworks()

    def on_action_bar_invoked(self, event: ActionBar.Invoked) -> None:
        action = {
            "notes": self.action_notes,
            "hint": self.action_hint,
            "undo": self.action_undo,
            "help": self.action_help,
            "menu": self.action_menu,
            "new": self.action_new,
            "review": self.action_review,
            "retry": self.action_restart,
            "quit": self.action_quit_app,
        }.get(event.action)
        if action:
            action()

    # -- actions ---------------------------------------------------------
    def action_cursor(self, dx: int, dy: int) -> None:
        cell = self.board.cursor
        r, c = (ROW[cell] + dy) % 9, (COL[cell] + dx) % 9
        self.board.cursor = r * 9 + c
        self._refresh_all()

    def action_notes(self) -> None:
        board = self.board
        board.notes_mode = not board.notes_mode
        self.note("Notes on: 1-9 now pencil small notes." if board.notes_mode
                  else "Notes off: 1-9 fill in digits again.")
        board.refresh()

    def action_fill_notes(self) -> None:
        if self.game.finished():
            return
        if self.game.fill_notes():
            self.note("Every possible note is pencilled in. [b]u[/b] takes them back.")
            self._after_action()
        else:
            self.note("The notes are already complete.")

    def action_clear(self) -> None:
        if self._editable() and self.game.clear(self.board.cursor):
            self._after_action()

    def action_undo(self) -> None:
        if self.game.finished():
            return
        if self.game.undo():
            self.note("Undone.")
            self._after_action()
        else:
            self.note("Nothing to undo.")

    def action_restart(self) -> None:
        g = self.game
        if g.failed:
            # Game over: the same puzzle again, from the start.
            app = self.app
            if isinstance(app, SudokuApp):
                app.new_game(replace=True, seed=g.seed, difficulty=g.difficulty, battle=g.battle)
            return
        if g.finished():
            return
        if self.game.restart():
            self.note("Started over. [b]u[/b] brings your digits back.")
            self._after_action()

    def action_hint(self) -> None:
        g, board = self.game, self.board
        if g.finished():
            return
        step = board.hint
        if step is not None:
            # Second press: do it.
            if step.technique == "mistake":
                g.clear(step.cells[0])
                self.note("Cleared.")
            elif step.place:
                cell, d = step.place
                g.set_value(cell, d, hint=True)
                self.note(f"Filled in the {d}.")
            self._after_action()
            return
        step = g.hint()
        if step is None:
            return
        board.hint = step
        if step.technique == "mistake":
            board.cursor = step.cells[0]
            self.note(f"[bold {HINT}]HINT[/] {step.why} [dim](h clears it)[/dim]")
        elif step.place:
            board.cursor = step.place[0]
            self.note(f"[bold {HINT}]HINT[/] {step.why} [dim](h fills it in)[/dim]")
        self._refresh_all()

    def action_enter(self) -> None:
        if self.game.finished():
            self.action_new()

    def action_new(self) -> None:
        app = self.app
        if isinstance(app, SudokuApp):
            app.new_game(replace=True)

    def action_escape(self) -> None:
        if self.board.hint is not None:
            self.board.hint = None
            self.note("")
            self._refresh_all()
            return
        self.action_menu()

    def action_menu(self) -> None:
        if not self.game.finished():
            self._save()
        self.app.pop_screen()

    def action_help(self) -> None:
        self.app.push_screen(TextPage("Help", HELP_TEXT))

    def action_review(self) -> None:
        g = self.game
        self.app.push_screen(review_page({
            "seed": g.seed, "difficulty": g.difficulty, "puzzle": grid_text(g.puzzle),
            "solution": grid_text(g.solution), "log": list(g.log), "won": g.is_solved()}))

    def action_quit_app(self) -> None:
        if not self.game.finished():
            self._save()
        self.app.exit()


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

MENU_INFO = {
    "continue": "Pick up your saved puzzle right where you left it.",
    "new": "A new puzzle at the difficulty below.",
    "anim": "Fireworks when you solve it (any key or click skips them).",
    "limit": "Game over after 3 mistakes (a wrong digit counts, also when mistakes are hidden).",
    "autonotes": "Placing a digit removes it from the notes in its row, column and box.",
    "highlight": "Light up the row, column and box of the selected cell.",
    "timer": "Show the clock while you play.",
    "whatsnew": "What changed in recent updates.",
    "help": "Rules, keys and mouse, hints.",
    "stats": "Solved puzzles, streaks, best times per difficulty.",
    "review": "Every move of your last puzzle: what was forced, what was a guess.",
    "quit": "See you next time.",
}

# A little 3x3 box for the menu, in the same style as the board.
MENU_BOX = [5, 3, 0, 6, 0, 0, 0, 9, 8]
MENU_NOTES = {2: (1, 2, 4), 4: (1, 7), 5: (2, 4)}


def menu_box() -> Text:
    cw, ch = BIG_CELL
    cv = Canvas(3 * cw + 2, 3 * ch + 2)
    cv.put(0, 0, "+" + "-" * (3 * cw) + "+", "dim")
    cv.put(0, 3 * ch + 1, "+" + "-" * (3 * cw) + "+", "dim")
    for y in range(1, 3 * ch + 1):
        cv.put(0, y, "|", "dim")
        cv.put(3 * cw + 1, y, "|", "dim")
    for i, v in enumerate(MENU_BOX):
        x, y = 1 + (i % 3) * cw, 1 + (i // 3) * ch
        if v:
            style = "bold" if i != 7 else f"bold {ACCENT}"
            for r, line in enumerate(BIG[v]):
                cv.put(x + 2, y + r, line, style)
        else:
            for d in MENU_NOTES.get(i, ()):
                cv.put(x + 1 + 2 * ((d - 1) % 3), y + (d - 1) // 3, str(d), "dim")
    return cv.to_text()


class MenuScreen(Screen):
    BINDINGS = [
        Binding("question_mark,f1", "help", "Help"),
        Binding("escape,q,ctrl+q", "quit_app", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        settings = load_settings()
        diff = settings.get("difficulty", "medium")
        mistakes = settings.get("mistakes", "off")
        items = [
            MenuItem("continue", "Continue game"),
            MenuItem("new", "New game"),
            MenuItem("difficulty", "Difficulty", "choice", [(d, d) for d in DIFFICULTIES],
                     index=DIFFICULTIES.index(diff) if diff in DIFFICULTIES else 1),
            MenuItem("mistakes", "Mistakes", "choice", [(MISTAKE_LABELS[m], m) for m in MISTAKE_MODES],
                     index=MISTAKE_MODES.index(mistakes) if mistakes in MISTAKE_MODES else 2),
            MenuItem("limit", "Mistake limit", "choice", [("3 mistakes", 3), ("off", 0)],
                     index=0 if int(settings.get("mistake_limit", 3) or 0) else 1),
            MenuItem("autonotes", "Auto notes", "choice", [("on", True), ("off", False)],
                     index=0 if settings.get("auto_notes", True) else 1),
            MenuItem("highlight", "Highlight", "choice", [("on", True), ("off", False)],
                     index=0 if settings.get("highlight", True) else 1),
            MenuItem("anim", "Win animation", "choice", [("on", True), ("off", False)],
                     index=0 if settings.get("win_animation", True) else 1),
            MenuItem("timer", "Clock", "choice", [("shown", True), ("hidden", False)],
                     index=0 if settings.get("timer", True) else 1),
            MenuItem("whatsnew", "What's new"),
            MenuItem("help", "Help"),
            MenuItem("review", "Review last game"),
            MenuItem("stats", "Statistics"),
            MenuItem("quit", "Quit"),
        ]
        with Center(id="menu-center"):
            with Vertical(id="menu-box"):
                yield Static(LOGO, id="menu-logo")
                yield Static("S U D O K U", id="menu-logo-small")
                yield Static(menu_box(), id="menu-art")
                yield Static(f"v{__version__}", id="menu-version")
                yield AsciiMenu(items, id="menu")
                yield Static("", id="menu-info", markup=True)
                yield ActionBar(
                    [("↑↓ select", None), ("←→ change", None), ("enter confirm", "activate"),
                     ("? help", "help"), ("q quit", "quit")],
                    id="menu-footer",
                )

    def on_mount(self) -> None:
        self._refresh_continue()
        self.query_one(AsciiMenu).focus()
        self._update_info()
        self._fit_art()

    def on_resize(self) -> None:
        self._fit_art()

    def _fit_art(self) -> None:
        height = self.size.height
        self.query_one("#menu-art", Static).display = height >= 42
        big = height >= 28 and self.size.width >= 66
        self.query_one("#menu-logo", Static).display = big
        self.query_one("#menu-logo-small", Static).display = not big

    def on_screen_resume(self) -> None:
        self._refresh_continue()
        self._fit_art()
        self.query_one(AsciiMenu).focus()

    def _refresh_continue(self) -> None:
        menu = self.query_one(AsciiMenu)
        snap = load_game_snapshot()
        menu.set_visible("continue", bool(snap and snap.get("game")))
        menu.set_visible("review", bool(load_game_logs()))
        self._update_info()

    def say(self, text: str) -> None:
        try:
            self.query_one("#menu-info", Static).update(f"[dim]{text}[/dim]")
        except Exception:  # noqa: BLE001 - not composed yet
            pass

    def _update_info(self) -> None:
        menu = self.query_one(AsciiMenu)
        item = menu.current_item()
        if item is None:
            return
        if item.id == "difficulty":
            info = DIFFICULTY_INFO[str(item.value)]
        elif item.id == "mistakes":
            info = MISTAKE_INFO[str(item.value)]
        elif item.id == "continue":
            snap = load_game_snapshot() or {}
            g = snap.get("game") or {}
            filled = sum(1 for ch in str(g.get("values", "")) if ch not in "0.")
            info = (f"Puzzle #{g.get('seed', '?')}, {g.get('difficulty', '?')}, {filled}/81 filled, "
                    f"{fmt_time(float(g.get('elapsed', 0)))} on the clock.")
        elif item.id == "new" and load_game_snapshot():
            info = "A new puzzle. Your saved puzzle then counts as not solved."
        else:
            info = MENU_INFO.get(item.id, "")
        self.query_one("#menu-info", Static).update(f"[dim]{info}[/dim]")

    @on(AsciiMenu.Highlighted)
    def _highlighted(self, _: AsciiMenu.Highlighted) -> None:
        self._update_info()

    @on(AsciiMenu.Changed)
    def _changed(self, event: AsciiMenu.Changed) -> None:
        settings = load_settings()
        key = {"difficulty": "difficulty", "mistakes": "mistakes", "limit": "mistake_limit",
               "autonotes": "auto_notes", "highlight": "highlight",
               "anim": "win_animation", "timer": "timer"}.get(event.item_id)
        if key:
            settings[key] = event.value
            save_settings(settings)
        self._update_info()

    @on(AsciiMenu.Activated)
    def _activated(self, event: AsciiMenu.Activated) -> None:
        app = self.app
        assert isinstance(app, SudokuApp)
        if event.item_id == "continue":
            app.continue_game()
        elif event.item_id == "new":
            app.new_game()
        elif event.item_id == "whatsnew":
            app.push_screen(TextPage("What's new", changelog_text()))
        elif event.item_id == "help":
            self.action_help()
        elif event.item_id == "stats":
            app.push_screen(TextPage("Statistics", "\n".join(stats_summary())))
        elif event.item_id == "review":
            logs = [g for g in load_game_logs() if g.get("puzzle") and g.get("solution")]
            if logs:
                app.push_screen(review_page(logs[-1]))
        elif event.item_id == "quit":
            app.exit()

    def action_help(self) -> None:
        self.app.push_screen(TextPage("Help", HELP_TEXT))

    def action_quit_app(self) -> None:
        self.app.exit()

    def on_action_bar_invoked(self, event: ActionBar.Invoked) -> None:
        if event.action == "activate":
            self.query_one(AsciiMenu).action_activate()
        elif event.action == "help":
            self.action_help()
        elif event.action == "quit":
            self.app.exit()


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


class SudokuApp(App):
    TITLE = f"Sudoku v{__version__}"

    BINDINGS = [Binding("f2", "screenshot", "Screenshot", show=False)]

    CSS = """
    Screen { background: ansi_default; color: ansi_default; }

    /* Menu */
    #menu-center { align: center middle; height: 1fr; }
    #menu-box { width: auto; height: auto; max-height: 100%; overflow-y: auto; scrollbar-size-vertical: 0; }
    #menu-logo { width: auto; }
    #menu-logo-small { width: auto; text-style: bold; margin-bottom: 1; }
    #menu-art { width: 100%; content-align: center top; margin-top: 1; }
    #menu-version { color: ansi_bright_black; margin-top: 1; margin-bottom: 1; }
    AsciiMenu { width: auto; height: auto; padding-left: 4; }
    #menu-info { height: 2; margin-top: 1; padding-left: 4; }
    #menu-footer { color: ansi_bright_black; }

    /* Game */
    #statusbar { height: 1; }
    Board { height: 1fr; width: 1fr; margin-top: 1; }
    #note { height: 1; }
    #footer-keys { height: 1; color: ansi_bright_black; }

    /* Text pages */
    #page-title { height: 1; text-style: bold; }
    #page-scroll {
        height: 1fr; scrollbar-size-vertical: 1;
        scrollbar-background: ansi_default; scrollbar-color: ansi_bright_black;
        scrollbar-background-hover: ansi_default; scrollbar-color-hover: ansi_white;
        scrollbar-background-active: ansi_default; scrollbar-color-active: ansi_white;
    }
    #page-body { padding: 1 2; }
    #page-footer { height: 1; color: ansi_bright_black; }
    """

    def __init__(self, no_update: bool = False, initial: Optional[dict] = None,
                 resume: bool = False) -> None:
        super().__init__(ansi_color=True)
        self.no_update = no_update
        self.initial = initial
        self.resume = resume
        self._restart_args: Optional[list[str]] = None
        self.window_focused = True
        self.last_input = time.monotonic()

    # -- the game clock only runs while you are actually playing ---------
    def clock_running(self) -> bool:
        return self.window_focused and time.monotonic() - self.last_input < IDLE_PAUSE

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, (events.Key, events.MouseDown, events.MouseUp, events.MouseMove,
                              events.MouseScrollDown, events.MouseScrollUp)):
            self.last_input = time.monotonic()
        await super().on_event(event)

    def on_app_blur(self) -> None:
        self.window_focused = False

    def on_app_focus(self) -> None:
        self.window_focused = True
        self.last_input = time.monotonic()

    def on_mount(self) -> None:
        self.push_screen(MenuScreen())
        if self.initial:
            if not self._resume_same_puzzle():
                self.new_game(seed=self.initial.get("seed"), difficulty=self.initial.get("difficulty"),
                              battle=self.initial.get("battle"))
        elif self.resume:
            self.continue_game()
        self._whats_new()
        if not self.no_update:
            self._update_check()

    # -- games -----------------------------------------------------------
    def _resume_same_puzzle(self) -> bool:
        """`--puzzle N` while a saved game of that same puzzle is waiting (a
        chat battle reopened, a restarted terminal): carry on with it instead
        of starting over and counting the saved one as not solved."""
        init = self.initial or {}
        snap = (load_game_snapshot() or {}).get("game") or {}
        if init.get("battle"):
            return snap.get("battle") == init["battle"] and self.continue_game()
        if init.get("seed") is None or snap.get("seed") != init.get("seed"):
            return False
        if init.get("difficulty") and snap.get("difficulty") != init.get("difficulty"):
            return False
        return self.continue_game()

    def _game_screen(self) -> Optional[GameScreen]:
        return next((s for s in self.screen_stack if isinstance(s, GameScreen)), None)

    def _abandon_saved(self) -> None:
        """A saved, started puzzle that gets replaced counts as not solved."""
        screen = self._game_screen()
        if screen is not None:
            screen.abandon()
        snap = load_game_snapshot()
        if not snap or not snap.get("game"):
            return
        try:
            game = Sudoku.from_dict(snap["game"])
        except Exception:  # noqa: BLE001 - unreadable save: just drop it
            clear_saved_game()
            return
        if game.started and not game.finished():
            record_game(
                {"won": False, "difficulty": game.difficulty, "time": int(game.elapsed),
                 "mistakes": game.mistakes, "hints": game.hints, "seed": game.seed,
                 **({"battle": game.battle} if game.battle else {})},
                game=game.to_dict(),
            )
        clear_saved_game()

    def _say(self, text: str) -> None:
        screen = self.screen
        if isinstance(screen, GameScreen):
            screen.note(text)
        elif isinstance(screen, MenuScreen):
            screen.say(text)

    def new_game(self, replace: bool = False, seed: Optional[int] = None,
                 difficulty: Optional[str] = None, battle: Optional[int] = None) -> None:
        settings = load_settings()
        difficulty = difficulty or str(settings.get("difficulty", "medium"))
        if difficulty not in DIFFICULTIES:
            difficulty = "medium"
        if seed is None:
            seed = random.SystemRandom().randint(1, 999_999)
        self._say(f"Making a {difficulty} puzzle...")
        self._make_puzzle(seed, difficulty, replace, battle)

    @work(thread=True, exclusive=True, group="puzzle")
    def _make_puzzle(self, seed: int, difficulty: str, replace: bool, battle: Optional[int] = None) -> None:
        game = Sudoku(seed, difficulty)  # a fraction of a second, even for expert
        game.battle = battle
        self.call_from_thread(self._start_game, game, replace)

    def _start_game(self, game: Sudoku, replace: bool) -> None:
        self._abandon_saved()
        screen = GameScreen(game)
        if replace and isinstance(self.screen, GameScreen):
            self.switch_screen(screen)
        else:
            self.push_screen(screen)

    def continue_game(self) -> bool:
        snap = load_game_snapshot()
        if not snap or not snap.get("game"):
            return False
        try:
            game = Sudoku.from_dict(snap["game"])
        except Exception:  # noqa: BLE001
            clear_saved_game()
            return False
        self.push_screen(GameScreen(game))
        return True

    # -- screenshots -----------------------------------------------------
    def action_screenshot(self) -> None:
        path = self.save_terminal_screenshot()
        if path:
            self.notify(f"Screenshot saved: {path}", timeout=6)
        else:
            self.notify("Screenshot failed.", severity="warning", timeout=6)

    def save_terminal_screenshot(self) -> Optional[str]:
        import datetime
        import re

        try:
            svg = self.export_screenshot()
            svg = re.sub(r"<circle[^>]*/>", "", svg)
            svg = re.sub(
                r"<text[^>]*>[^<]*</text>",
                lambda m: "" if self.TITLE in m.group(0) else m.group(0),
                svg,
                count=3,
            )
            target = Path.home() / "Downloads"
            if not target.is_dir():
                target = Path.home()
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = target / f"sudoku-{stamp}.svg"
            path.write_text(svg)
            return str(path)
        except Exception:  # noqa: BLE001
            return None

    # -- updates ---------------------------------------------------------
    def _whats_new(self) -> None:
        from ..config import SETTINGS_FILE

        first_run = not SETTINGS_FILE.exists()
        settings = load_settings()
        if settings.get("last_seen_version") == __version__:
            return
        settings["last_seen_version"] = __version__
        save_settings(settings)
        if first_run:
            return
        summary = _changelog_summary(__version__)
        if summary:
            self.notify(summary, title=f"What's new in v{__version__}", timeout=12)

    @work(thread=True, exclusive=True, group="update")
    def _update_check(self) -> None:
        result = check_and_update()
        if result.status == "updated":
            self.call_from_thread(self._apply_background_update, result)
        elif result.status == "failed":
            self.call_from_thread(
                self.notify, result.message, title="Update", severity="warning", timeout=10
            )

    def _apply_background_update(self, result) -> None:
        """A background update landed: restart now from the menu, or tell
        the player it applies on the next start (the game is saved)."""
        if self._game_screen() is None:
            self.request_restart()
            return
        self.notify(
            f"Update installed: v{result.new_version or '?'}. It starts next time "
            f"(your game is saved).",
            title="Update",
            timeout=10,
        )

    def request_restart(self, resume: bool = False) -> None:
        self._restart_args = ["--no-update"] + (["--resume"] if resume else [])
        self.exit()


def run(no_update: bool = False, initial: Optional[dict] = None, resume: bool = False) -> None:
    app = SudokuApp(no_update=no_update, initial=initial, resume=resume)
    app.run()
    if app._restart_args is not None:
        os.execv(sys.executable, [sys.executable, "-m", "sudoku_app", *app._restart_args])
