"""Chooser: decides which expression and short line to use next.

The default is rule based and deterministic. Any other chooser (such as the optional
LLM one) can only pick from the Menu the loop offers; `pick` throws away anything else
and uses the rule-based choice. Text from sensors, the scene or a model is data.
"""

import logging
import random
from dataclasses import dataclass
from typing import Protocol

from beh_personality import Personality, valid_utterance

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Menu:
    """Everything a chooser may return: TD expressions and fixed short lines."""

    expressions: tuple[str, ...]
    utterances: tuple[str, ...]
    max_chars: int

    @staticmethod
    def from_personality(p: Personality) -> "Menu":
        return Menu(p.react_exprs, p.utterances, p.max_utterance_chars)


@dataclass(frozen=True)
class Choice:
    expression: str
    utterance: str | None = None


class Chooser(Protocol):
    def choose(self, state: str, menu: Menu) -> Choice | None: ...


def validate_choice(raw: object, menu: Menu) -> Choice | None:
    """Return the choice only if every field is exactly a menu option."""
    if not isinstance(raw, Choice):
        return None
    if raw.expression not in menu.expressions:
        return None
    if raw.utterance is not None and not (
        raw.utterance in menu.utterances and valid_utterance(raw.utterance, menu.max_chars)
    ):
        return None
    return raw


class RuleChooser:
    """Seeded random pick from the menu. Same rng state gives the same choice."""

    def __init__(self, rng: random.Random, utterance_p: float) -> None:
        self._rng, self._p = rng, utterance_p

    def choose(self, state: str, menu: Menu) -> Choice | None:
        expression = self._rng.choice(menu.expressions)
        said = menu.utterances and self._rng.random() < self._p
        return Choice(expression, self._rng.choice(menu.utterances) if said else None)


def pick(primary: Chooser, fallback: RuleChooser, state: str, menu: Menu) -> Choice:
    """Ask `primary`; on any error or out-of-menu answer use the rule-based choice."""
    try:
        choice = validate_choice(primary.choose(state, menu), menu)
    except Exception:
        logger.exception("chooser failed, using rule-based choice")
        choice = None
    if choice is None:
        logger.warning("chooser answer rejected, using rule-based choice")
        choice = validate_choice(fallback.choose(state, menu), menu)
    if choice is None:  # a menu with nothing usable: stay neutral and silent
        return Choice(menu.expressions[0])
    return choice
