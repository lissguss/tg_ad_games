"""Правила игры «Взломайте телефон». Чистая логика, без Telegram и базы данных."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

PASSWORD = "171126"   # ДДММГГ, один на всех
CODE_LENGTH = len(PASSWORD)
MAX_ATTEMPTS = 5

HIT = "hit"    # цифра на своём месте — зелёный
NEAR = "near"  # цифра есть, но не там — синий
MISS = "miss"  # цифры нет (или все её вхождения уже отмечены) — серый

PLAYING, WON, LOST = "playing", "won", "lost"

# Тексты всплывающих уведомлений
MSG_FULL = f"Уже {CODE_LENGTH} цифр — нажмите «Проверить»"
MSG_NEED_ALL = f"Введите {CODE_LENGTH} цифр"
MSG_EMPTY = "Нечего удалять"
MSG_FINISHED = "Игра окончена — нажмите «Играть снова»"


def score(secret: str, guess: str) -> list[str]:
    """Отметки как в Wordle: цифры в пароле повторяются, поэтому синих для цифры
    не больше, чем её вхождений в пароле, не занятых зелёными."""
    marks = [HIT if g == s else MISS for g, s in zip(guess, secret)]
    left = Counter(s for s, m in zip(secret, marks) if m != HIT)
    for i, g in enumerate(guess):
        if marks[i] != HIT and left[g] > 0:
            marks[i] = NEAR
            left[g] -= 1
    return marks


@dataclass
class Attempt:
    guess: str
    marks: list[str]


@dataclass
class Game:
    secret: str = PASSWORD
    current: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    status: str = PLAYING

    @property
    def finished(self) -> bool:
        return self.status in (WON, LOST)

    @property
    def attempt_number(self) -> int:
        """Номер текущей (ещё не проверенной) попытки, 1..5."""
        return min(len(self.attempts) + 1, MAX_ATTEMPTS)

    def press_digit(self, digit: str) -> str | None:
        """Возвращает текст уведомления, если нажатие не применено."""
        if self.finished:
            return MSG_FINISHED
        if len(self.current) >= CODE_LENGTH:
            return MSG_FULL
        self.current += digit
        return None

    def press_delete(self) -> str | None:
        if self.finished:
            return MSG_FINISHED
        if not self.current:
            return MSG_EMPTY
        self.current = self.current[:-1]
        return None

    def press_check(self) -> str | None:
        if self.finished:
            return MSG_FINISHED
        if len(self.current) != CODE_LENGTH:
            return MSG_NEED_ALL
        marks = score(self.secret, self.current)
        self.attempts.append(Attempt(self.current, marks))
        self.current = ""
        if all(m == HIT for m in marks):
            self.status = WON
        elif len(self.attempts) >= MAX_ATTEMPTS:
            self.status = LOST
        return None
