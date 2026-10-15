"""Правила игры «Мемори». Чистая логика, без Telegram и базы данных."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

EMOJIS = ("👑", "🎓", "👷‍♂️")
COPIES = 4                      # каждого эмодзи по 4 карточки
CARDS = len(EMOJIS) * COPIES    # 12
COLUMNS = 3
FLIP_DELAY = 1.5                # секунд от открытия второй карточки до переворота

PLAYING, WON = "playing", "won"

# Тексты всплывающих уведомлений
MSG_ALREADY_OPEN = "Эта карточка уже открыта"
MSG_FINISHED = "Все пары найдены — нажмите «Сыграть ещё»"


def generate_layout(rng: random.Random | None = None) -> list[str]:
    rng = rng or random.SystemRandom()
    layout = [e for e in EMOJIS for _ in range(COPIES)]
    rng.shuffle(layout)
    return layout


@dataclass
class Game:
    layout: list[str]
    opened: list[int] = field(default_factory=list)  # открытые в текущем ходе (0, 1 или 2)
    found: list[int] = field(default_factory=list)   # карточки из найденных пар
    status: str = PLAYING
    flip_at: float | None = None  # когда перевернуть несовпавшую пару (unix-время)

    @property
    def mismatch(self) -> bool:
        """Открыты две несовпавшие карточки и ждут переворота."""
        return len(self.opened) == 2

    def is_face_up(self, i: int) -> bool:
        return i in self.opened or i in self.found

    def flip_back(self) -> bool:
        """Перевернуть несовпавшую пару. True, если было что переворачивать."""
        if not self.mismatch:
            return False
        self.opened = []
        self.flip_at = None
        return True

    def press(self, i: int, now: float) -> tuple[str | None, bool]:
        """Нажатие на карточку i. Возвращает (уведомление, изменилось ли поле)."""
        if self.status == WON:
            return MSG_FINISHED, False
        if not 0 <= i < CARDS:
            return None, False
        if self.is_face_up(i):
            return MSG_ALREADY_OPEN, False
        # нажали новую карточку, пока показывается несовпавшая пара: сразу переворачиваем пару
        self.flip_back()
        self.opened.append(i)
        if len(self.opened) == 2:
            a, b = self.opened
            if self.layout[a] == self.layout[b]:
                self.found += [a, b]
                self.opened = []
                if len(self.found) == CARDS:
                    self.status = WON
            else:
                self.flip_at = now + FLIP_DELAY
        return None, True
