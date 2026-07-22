"""Reading/writing delivery state for ads sent to messenger platforms."""
import json
import os

_TOKENS_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "tokens.json")


def load_state():
    """Load delivery state, migrating the legacy token list when needed."""
    try:
        with open(_TOKENS_PATH, "r") as content:
            data = content.read()
            if not data:
                return {"known_tokens": [], "delivered": {}}
            parsed = json.loads(data)
            if isinstance(parsed, list):
                return {"known_tokens": parsed, "delivered": {"telegram": parsed}}
            if not isinstance(parsed, dict):
                print(
                    "Warning: tokens.json did not contain a list or object "
                    "(got {}), resetting to empty list.".format(type(parsed))
                )
                return {"known_tokens": [], "delivered": {}}

            known_tokens = parsed.get("known_tokens", [])
            delivered = parsed.get("delivered", {})
            if not isinstance(known_tokens, list) or not isinstance(delivered, dict):
                print("Warning: invalid tokens.json state, resetting to empty state.")
                return {"known_tokens": [], "delivered": {}}
            return {"known_tokens": known_tokens, "delivered": delivered}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"known_tokens": [], "delivered": {}}


def save_state(state):
    with open(_TOKENS_PATH, "w") as outfile:
        json.dump(state, outfile, ensure_ascii=False, sort_keys=True)
