# Changelog

## v1.0.1 (2026-11-09)
- New defaults: difficulty easy, mistakes hidden. Settings you already chose stay as they are.

## v1.0.0 (2026-11-09)
- Sudoku in the terminal, in the same ASCII style as yahtzee and solitaire.
- Big digits, pencil notes (n, shift+digit, or f for all of them), unlimited undo.
- Four difficulties, rated by a built-in logical solver; every puzzle has exactly one solution.
- Hints that explain the next step (singles, pairs, pointing, X-wings) and fill it in on a second press.
- Mistakes shown as wrong digits, as conflicts, or not at all.
- Numbered puzzles (`--puzzle N`), auto-save and continue, statistics per difficulty, win fireworks.
- Every move of every puzzle is kept in games.jsonl for reviews.
- The clock pauses when the terminal is in the background or you are away.
- One-line installer and Claude-style auto-update on every start.
