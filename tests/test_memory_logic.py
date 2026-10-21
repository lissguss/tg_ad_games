import random
from collections import Counter

from games.memory.logic import (
    CARDS, EMOJIS, FLIP_DELAY, MSG_ALREADY_OPEN, MSG_FINISHED, PLAYING, WON, Game, generate_layout,
)

# известная раскладка: 👑 на 0,1,2,3; 🎓 на 4..7; ⛑️ на 8..11
LAYOUT = [EMOJIS[i // 4] for i in range(CARDS)]


def game() -> Game:
    return Game(layout=list(LAYOUT))


def test_layout_has_four_of_each_emoji():
    rng = random.Random(1)
    for _ in range(500):
        layout = generate_layout(rng)
        assert len(layout) == 12
        assert Counter(layout) == {e: 4 for e in EMOJIS}


def test_layout_is_random():
    rng = random.Random(2)
    assert len({tuple(generate_layout(rng)) for _ in range(50)}) > 1


def test_match_stays_open():
    g = game()
    assert g.press(0, 0) == (None, True)
    assert g.press(3, 0) == (None, True)
    assert g.found == [0, 3]
    assert g.opened == []
    assert g.flip_at is None


def test_mismatch_flips_after_two_seconds_from_second_card():
    g = game()
    g.press(0, now=100)
    g.press(4, now=105)
    assert g.opened == [0, 4]
    assert g.flip_at == 105 + FLIP_DELAY
    assert g.flip_back()
    assert g.opened == [] and g.found == []


def test_press_open_card_changes_nothing():
    g = game()
    g.press(0, 0)
    assert g.press(0, 0) == (MSG_ALREADY_OPEN, False)
    g.press(1, 0)
    assert g.press(1, 0) == (MSG_ALREADY_OPEN, False)
    assert g.found == [0, 1] and g.opened == []


def test_press_card_of_shown_mismatch_changes_nothing():
    g = game()
    g.press(0, 0)
    g.press(4, 0)
    assert g.press(4, 0) == (MSG_ALREADY_OPEN, False)
    assert g.opened == [0, 4]


def test_new_card_during_mismatch_flips_pair_and_opens_card():
    g = game()
    g.press(0, 0)
    g.press(4, 0)
    assert g.press(8, 1) == (None, True)
    assert g.opened == [8]
    assert g.flip_at is None


def test_win_and_no_move_limit():
    g = game()
    for _ in range(100):  # попыток сколько угодно
        g.press(0, 0)
        g.press(4, 0)
        g.flip_back()
    for a, b in [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11)]:
        assert g.status == PLAYING
        g.press(a, 0)
        g.press(b, 0)
    assert g.status == WON
    assert sorted(g.found) == list(range(CARDS))
    assert g.press(0, 0) == (MSG_FINISHED, False)
