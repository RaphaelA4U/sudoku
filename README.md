# Sudoku TUI

Sudoku in your terminal, Claude-terminal style: your terminal's own colors,
everything in ASCII, no buttons and no dialogs. Big digits, pencil notes,
four difficulties, hints that explain the next step the way a person would
solve it, unlimited undo, statistics and win fireworks. Play with the
**keyboard** (arrows and 1 to 9) or the **mouse** (click a cell, click a
digit).

Made the same way as [yahtzee](https://github.com/RaphaelA4U/yahtzee) and
[solitaire](https://github.com/RaphaelA4U/solitaire): one-line install,
Claude-style auto-update on every start.

## Install

One line for macOS, Linux, WSL, iSH on iOS, web/SSH terminals, and Git Bash
on Windows:

```sh
U=https://raw.githubusercontent.com/RaphaelA4U/sudoku/main/install.sh; (curl -fsSL $U 2>/dev/null || wget -qO- $U) | sh
```

On native Windows without Git Bash, use PowerShell:

```powershell
iwr -useb https://raw.githubusercontent.com/RaphaelA4U/sudoku/main/install.ps1 | iex
```

Then start the game with:

```sh
sudoku
```

Requirements: `git` and `python3` 3.9+ (the installer offers to install
missing ones via your package manager). Everything lives in `~/.sudoku`;
the `sudoku` command is linked into `~/.local/bin`.

## Auto-update

When you type `sudoku`, a pending update is pulled before the game starts
(4 second timeout, so offline starts stay fast). An update that lands while
you sit in the menu restarts the game right away; mid-game it applies on the
next start, and your game is saved either way.

## How to play

Fill the grid so every row, every column and every 3x3 box holds 1 to 9
exactly once. Every puzzle has exactly one solution.

| Keys | |
|---|---|
| arrows | move around the grid |
| 1 to 9 | fill in a digit (the same digit again clears it; a filled cell must be cleared first) |
| n | notes mode on/off: 1 to 9 then pencil small notes |
| shift + 1 to 9 | pencil a note right away, in either mode |
| 0 / backspace / x | clear the cell |
| f | pencil in every possible note at once |
| h | hint: the next step and why; h again fills it in |
| u / ctrl+z | undo |
| r | start the puzzle over (undo brings it back) |
| v | review: every move, what was forced and what was a guess |
| esc | back to the menu (auto-saved) |
| ? | help, F2 screenshot (SVG into Downloads) |

| Mouse | |
|---|---|
| click a cell | select it |
| click a digit below the grid | fill it in (or pencil it in notes mode) |
| click "notes" | notes mode on/off |

Digits you start with are plain, the ones you fill in are purple, and the
digit you are on lights up cyan everywhere, notes included. With room for it
(about 73 x 43) the grid gets thin lines between all cells; smaller
terminals get a roomy grid without them, and very small ones a compact grid.

## Options

- **Difficulty**: easy and medium (the default) only need looking around
  (singles); hard needs pairs and pointing; expert needs X-wings, triples
  and XY-wings. A built-in logical solver rates every puzzle, so the label
  is honest.
- **Mistakes**: hidden (the default), show conflicts (red only on a clash in
  a row, column or box), or show wrong digits (red right away).
- **Mistake limit**: 3 mistakes (the default) ends the game, like sudoku.com;
  `r` then tries the same puzzle again. Or off.
- **Auto notes**: placing a digit removes it from the notes in its row,
  column and box (default on); clearing the digit puts them back.
- **Highlight**: the row, column and box of the selected cell light up
  (default on).
- **Win animation**: fireworks when you solve it (any key or click skips).
- **Clock**: shown or hidden. It pauses when the terminal is in the
  background or after two minutes without input.

Every puzzle has a number. `sudoku --puzzle 123 --difficulty hard` plays
exactly that puzzle again.

```
sudoku --new                      a new puzzle right away
sudoku --difficulty expert        easy | medium | hard | expert
sudoku --puzzle 42                a specific puzzle
sudoku --resume                   reopen the saved puzzle
sudoku --battle ID                a beheer chat battle: the same puzzle for everyone
sudoku --profile NAME             separate settings, stats, and save
```

Settings, statistics and the saved game live in `~/.config/sudoku/`. Every
move of every puzzle is kept in `~/.config/sudoku/games.jsonl` (a few KB per
puzzle), so games can be replayed and reviewed afterwards.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Releases: bump the version in `pyproject.toml` and `sudoku_app/__init__.py`
(both), add a CHANGELOG entry, commit as `vX.Y.Z title`, tag `vX.Y.Z`, and
push main plus the tag. Installed copies update themselves on their next
start.
