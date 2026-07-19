"""Reading/writing the tokens.json file that tracks which ads we've
already notified about."""
import json
import os

_TOKENS_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "tokens.json")


def load_tokens():
    try:
        with open(_TOKENS_PATH, "r") as content:
            data = content.read()
            if not data:
                return []
            parsed = json.loads(data)
            if not isinstance(parsed, list):
                print(
                    "Warning: tokens.json did not contain a list "
                    "(got {}), resetting to empty list.".format(type(parsed))
                )
                return []
            return parsed
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_tokens(tokens):
    with open(_TOKENS_PATH, "w") as outfile:
        json.dump(tokens, outfile)
