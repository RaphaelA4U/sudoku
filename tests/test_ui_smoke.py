"""Drive the real app headless: menu, keys, mouse, hints, solving, saving."""

import pytest

from sudoku_app import config
from sudoku_app.game import Sudoku, grid_text
from sudoku_app.ui.app import (
    AsciiMenu,
    Board,
    GameScreen,
    MenuScreen,
    SudokuApp,
    TextPage,
)

SIZE = (139, 43)


async def start(pilot, app, cls=GameScreen):
    for _ in range(100):
        await pilot.pause(0.05)
        if isinstance(app.screen, cls):
            return app.screen
    raise AssertionError(f"no {cls.__name__} (got {app.screen!r})")


def empties(g):
    return [i for i in range(81) if not g.puzzle[i]]


async def goto(pilot, screen, cell):
    screen.board.cursor = cell
    await pilot.pause()


async def test_menu_boots_and_starts_a_game():
    app = SudokuApp(no_update=True)
    async with app.run_test(size=SIZE) as pilot:
        await start(pilot, app, MenuScreen)
        menu = app.screen.query_one(AsciiMenu)
        assert menu.current_item().id == "new"  # no saved game yet
        await pilot.press("down", "right")  # difficulty easy -> medium
        assert config.load_settings()["difficulty"] == "medium"
        await pilot.press("up", "enter")
        screen = await start(pilot, app)
        assert screen.game.difficulty == "medium"


async def test_help_and_stats_pages():
    app = SudokuApp(no_update=True)
    async with app.run_test(size=SIZE) as pilot:
        await start(pilot, app, MenuScreen)
        await pilot.press("question_mark")
        assert isinstance(app.screen, TextPage)
        await pilot.press("escape")
        assert isinstance(app.screen, MenuScreen)


async def test_keyboard_digits_notes_clear_undo():
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 3})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g = screen.game
        cell = empties(g)[0]
        await goto(pilot, screen, cell)
        d = g.solution[cell]
        await pilot.press(str(d))
        assert g.values[cell] == d
        await pilot.press(str(d))  # same digit again clears
        assert g.values[cell] == 0
        await pilot.press("n", "1", "2")  # notes mode
        assert g.notes[cell] == (1 << 1) | (1 << 2)
        await pilot.press("n", "exclamation_mark")  # shift+1 toggles a note anyway
        assert g.notes[cell] == 1 << 2
        await pilot.press("backspace")
        assert g.notes[cell] == 0
        await pilot.press("u")
        assert g.notes[cell] == 1 << 2
        # Arrow keys move and wrap around.
        screen.board.cursor = 0
        await pilot.press("left", "up")
        assert screen.board.cursor == 80


async def test_given_digits_cannot_change():
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 3})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g = screen.game
        given = next(i for i in range(81) if g.puzzle[i])
        await goto(pilot, screen, given)
        await pilot.press(str(g.puzzle[given] % 9 + 1), "backspace")
        assert g.values[given] == g.puzzle[given] and not g.started


async def test_mouse_cell_and_digit_row():
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 3})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g, board = screen.game, screen.board
        cell = empties(g)[1]
        x, y = board.cell_xy(cell)
        await pilot.click(Board, offset=(x + 3, y + 1))
        assert board.cursor == cell
        d = g.solution[cell]
        digit = next(r for r in board._rects if r[4] == "digit" and r[5] == d)
        await pilot.click(Board, offset=(digit[0] + 1, digit[1]))
        assert g.values[cell] == d
        notes = next(r for r in board._rects if r[4] == "notes")
        await pilot.click(Board, offset=(notes[0] + 1, notes[1]))
        assert board.notes_mode


async def test_hint_twice_fills_in():
    app = SudokuApp(no_update=True, initial={"difficulty": "medium", "seed": 8})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g = screen.game
        await pilot.press("h")
        step = screen.board.hint
        assert step is not None and step.place
        assert "HINT" in screen._note_text and screen.board.cursor == step.place[0]
        await pilot.press("h")
        cell, d = step.place
        assert g.values[cell] == d and g.hints == 1 and screen.board.hint is None


async def test_solving_records_stats_log_and_fireworks():
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 21})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g = screen.game
        todo = empties(g)
        for cell in todo[:-1]:
            g.set_value(cell, g.solution[cell])
        await goto(pilot, screen, todo[-1])
        await pilot.press(str(g.solution[todo[-1]]))
        assert g.is_solved()
        assert screen._fx_timer is not None and screen.board.banner
        stats = config.load_stats()["games"]
        assert [s["won"] for s in stats] == [True] and stats[0]["difficulty"] == "easy"
        logs = config.load_game_logs()
        assert len(logs) == 1 and logs[0]["puzzle"] == grid_text(g.puzzle)
        again = Sudoku.replay(21, "easy", logs[0]["puzzle"], logs[0]["solution"], logs[0]["log"])
        assert again.is_solved()
        assert config.load_game_snapshot() is None
        await pilot.press("x")  # any key only skips the fireworks
        assert screen._fx_timer is None and g.is_solved()
        await pilot.press("enter")  # a new puzzle
        for _ in range(100):
            await pilot.pause(0.05)
            if isinstance(app.screen, GameScreen) and app.screen.game is not g:
                break
        assert app.screen.game is not g


async def test_save_continue_and_new_counts_unsolved():
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 5})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g = screen.game
        cell = empties(g)[0]
        await goto(pilot, screen, cell)
        await pilot.press("7")
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        menu = app.screen.query_one(AsciiMenu)
        assert menu.visible_items()[0].id == "continue"
        await pilot.press("enter")
        resumed = await start(pilot, app)
        assert resumed.game.values[cell] == 7 and resumed.game.can_undo()
        await pilot.press("escape")
        await start(pilot, app, MenuScreen)
        await pilot.press("down", "enter")  # New game
        for _ in range(100):
            await pilot.pause(0.05)
            if isinstance(app.screen, GameScreen) and app.screen.game.seed != 5:
                break
        assert config.load_stats()["games"][0]["won"] is False
        assert config.load_game_logs()[0]["log"][0].startswith("v")


async def test_small_terminal_uses_compact_cells():
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 3})
    async with app.run_test(size=(60, 22)) as pilot:
        screen = await start(pilot, app)
        assert (screen.board.cw, screen.board.ch) == (3, 1)
        for key in ["right", "down", "1", "n", "2", "h", "h", "f", "u", "escape"]:
            await pilot.press(key)
        await pilot.pause()


async def test_layout_picks_thin_lines_when_there_is_room():
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 3})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        assert screen.board.thin and screen.board.ch == 3
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 3})
    async with app.run_test(size=(100, 38)) as pilot:
        screen = await start(pilot, app)
        assert not screen.board.thin and screen.board.ch == 3


async def test_clock_pauses_in_background_and_when_idle():
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 3})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g = screen.game
        await goto(pilot, screen, empties(g)[0])
        await pilot.press("5")
        t = g.elapsed
        screen._tick()
        assert g.elapsed == t + 1
        app.on_app_blur()
        screen._tick()
        assert g.elapsed == t + 1 and "paused" in screen._status_text().plain
        app.on_app_focus()
        screen._tick()
        assert g.elapsed == t + 2
        app.last_input -= 1000
        screen._tick()
        assert g.elapsed == t + 2


async def test_review_in_game_and_from_menu():
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 3})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g = screen.game
        cells = empties(g)
        await goto(pilot, screen, cells[0])
        await pilot.press(str(g.solution[cells[0]] % 9 + 1))  # a wrong digit
        await pilot.press("v")
        assert isinstance(app.screen, TextPage)
        body = str(app.screen.query_one("#page-body").render())
        assert "Every action" in body and "wrong, it is" in body
        await pilot.press("escape", "escape")
        await start(pilot, app, MenuScreen)
        await pilot.press("down", "enter")  # New game: the old one is recorded
        await start(pilot, app)
        await pilot.press("escape")
        await start(pilot, app, MenuScreen)
        assert app.screen.query_one(AsciiMenu).item("review").visible


async def test_same_puzzle_number_resumes_the_saved_game():
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 61})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        cell = empties(screen.game)[0]
        await goto(pilot, screen, cell)
        await pilot.press("4", "escape")
        await pilot.pause()
    app = SudokuApp(no_update=True, initial={"difficulty": "easy", "seed": 61})
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        assert screen.game.values[cell] == 4
    assert config.load_stats()["games"] == []


async def test_battle_puzzle_is_shared_and_resumes():
    from sudoku_app.game import battle_seed

    init = {"difficulty": "easy", "seed": battle_seed(12), "battle": 12}
    app = SudokuApp(no_update=True, initial=dict(init))
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        g = screen.game
        assert g.battle == 12 and g.seed == battle_seed(12) and "Battle" in screen._status_text().plain
        cell = empties(g)[0]
        await goto(pilot, screen, cell)
        await pilot.press(str(g.solution[cell]), "escape")
        await pilot.pause()
    assert config.load_game_snapshot()["game"]["battle"] == 12
    app = SudokuApp(no_update=True, initial=dict(init))
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app)
        assert screen.game.battle == 12 and screen.game.values[cell]
