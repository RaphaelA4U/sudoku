# Changelog

## v1.3.1 (2026-11-11)
- The review recognises a short chain as proof: when a digit follows from at most three steps (for example an XY-wing, then a single elsewhere, then this digit), it counts as proven and shows the steps, instead of being called a choice. Longer chains still count as a choice, so a lucky guess does not look like proof.

## v1.3.0 (2026-11-11)
- The review now counts a digit found with pointing, a pair or another technique as proven, not as a guess, and shows the step that proves it.
- When your notes showed only one option while more were still possible, the review says which note was missing (and when a wrong digit of yours ruled it out).

## v1.2.1 (2026-11-10)
- New defaults: difficulty hard, mistakes "show conflicts", mistake limit off. Settings you already chose stay as they are.

## v1.2.0 (2026-11-09)
- Mistake limit (default 3): the third wrong digit is game over, like sudoku.com; r tries the same puzzle again. The count shows as "Mistakes 1/3". Switch it off in the menu.
- Highlight (default on): the row, column and box of the selected cell light up.
- Auto notes is now a setting (default on). Clearing a digit puts back the notes it had removed, so a wrong digit no longer leaves your notes incomplete.

## v1.1.4 (2026-11-09)
- A filled cell is no longer overwritten by another digit: clear it first (backspace or 0). A misclick on the digit row now does nothing instead of counting as a mistake.

## v1.1.3 (2026-11-09)
- New defaults: difficulty medium, mistakes hidden. Settings you already chose stay as they are.

## v1.1.2 (2026-11-09)
- Saved games carry a `progress` count (digits filled), so a beheer race reads every race game the same way.

## v1.1.1 (2026-11-09)
- Mistakes now default to "show conflicts": a digit turns red when it clashes with its row, column or box, without giving the solution away.

## v1.1.0 (2026-11-09)
- Review (v, or "Review last game" in the menu): every move, which digits were forced and which were a guess, clashes, the digits still wrong, the longest pauses, and tips.
- Chat battles: `sudoku --battle ID` gives everyone of a beheer battle the same puzzle; reopening carries on with it.
- `--puzzle N` carries on with a saved game of that same puzzle instead of starting over.

## v1.0.2 (2026-11-09)
- Hovering over a cell shows four small corners around it, instead of underlining the big digit.

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
