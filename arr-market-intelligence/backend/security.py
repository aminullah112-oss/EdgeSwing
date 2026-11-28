"""Single-user access control: one shared bearer key plus one-time login state."""
import hmac
import secrets
import time

from backend import config

MIN_KEY_LENGTH = 24
STATE_TTL_SECONDS = 600
_states: dict[str, float] = {}


def configured() -> bool:
    return len(config.APP_ACCESS_KEY) >= MIN_KEY_LENGTH


def key_matches(candidate) -> bool:
    if not configured() or not isinstance(candidate, str):
        return False
    return hmac.compare_digest(candidate.encode(), config.APP_ACCESS_KEY.encode())


def issue_state() -> str:
    now = time.monotonic()
    for old in [k for k, expiry in _states.items() if expiry < now]:
        _states.pop(old, None)
    state = secrets.token_urlsafe(24)
    _states[state] = now + STATE_TTL_SECONDS
    return state


def consume_state(state) -> bool:
    expiry = _states.pop(state or '', None)
    return expiry is not None and expiry >= time.monotonic()
