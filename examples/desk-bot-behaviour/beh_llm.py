"""Optional LLM-backed chooser. Off by default and never used by tests.

It makes a network call only when ALL of these environment variables are set:
THINGWIRE_BEHAVIOUR_LLM=1, THINGWIRE_LLM_URL (https), THINGWIRE_LLM_KEY and
THINGWIRE_LLM_MODEL. The prompt holds only the state name and the menu, never sensor
or scene text. The reply is parsed into a Choice that `beh_chooser.pick` then checks
against the menu, so the model can only select from what the loop offers.
"""

import json
import logging
import urllib.request
from collections.abc import Callable, Mapping

from beh_chooser import Choice, Menu

logger = logging.getLogger(__name__)

Complete = Callable[[str], str]
ENABLE, URL, KEY, MODEL = (
    "THINGWIRE_BEHAVIOUR_LLM",
    "THINGWIRE_LLM_URL",
    "THINGWIRE_LLM_KEY",
    "THINGWIRE_LLM_MODEL",
)
TIMEOUT = "THINGWIRE_LLM_TIMEOUT_S"


def build_prompt(state: str, menu: Menu) -> str:
    return (
        f"A desk robot is in state {state}. Pick one expression and optionally one line "
        f"from these lists. Reply with JSON only: "
        f'{{"expression": <one of {list(menu.expressions)}>, '
        f'"utterance": <one of {list(menu.utterances)} or null>}}'
    )


class LlmChooser:
    """Turns a text completion into an (unchecked) Choice."""

    def __init__(self, complete: Complete) -> None:
        self._complete = complete

    def choose(self, state: str, menu: Menu) -> Choice | None:
        try:
            data = json.loads(self._complete(build_prompt(state, menu)))
        except (ValueError, OSError):
            logger.warning("LLM reply unusable")
            return None
        if not isinstance(data, dict):
            return None
        expression, utterance = data.get("expression"), data.get("utterance")
        if not isinstance(expression, str) or not (utterance is None or isinstance(utterance, str)):
            return None
        return Choice(expression, utterance)


def http_complete(url: str, key: str, model: str, timeout_s: float) -> Complete:
    """Chat-completions style POST. Only built when the owner enabled it."""

    def complete(prompt: str) -> str:
        body = json.dumps(
            {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 80}
        ).encode()
        req = urllib.request.Request(
            url,
            body,
            {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            reply = json.loads(resp.read())
        return str(reply["choices"][0]["message"]["content"])

    return complete


def llm_from_env(env: Mapping[str, str]) -> LlmChooser | None:
    """The LLM chooser if the owner opted in with url, key and model; otherwise None."""
    if env.get(ENABLE) != "1":
        return None
    url, key, model = env.get(URL, ""), env.get(KEY, ""), env.get(MODEL, "")
    if not (url.startswith("https://") and key and model):
        logger.warning("LLM chooser requested but url, key or model is missing: not used")
        return None
    try:
        timeout = float(env.get(TIMEOUT, "5"))
    except ValueError:
        return None
    return LlmChooser(http_complete(url, key, model, timeout))
