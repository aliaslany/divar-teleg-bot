"""Reading/writing the tokens.json file that tracks which ads we've
already notified about."""
import errno
import json
import os
import shutil
import tempfile

_TOKENS_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "tokens.json")
_PHONES_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "phones.json")


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


def _save_json(path, data, **dump_options):
    """Skip unchanged state and serialize fully before replacing existing data."""
    try:
        with open(path, "r", encoding="utf-8") as current:
            if json.load(current) == data:
                return False
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=os.path.dirname(os.path.abspath(path)),
            prefix=".{}-".format(os.path.basename(path)),
            suffix=".tmp",
            delete=False,
        ) as outfile:
            temporary_path = outfile.name
            json.dump(data, outfile, **dump_options)
            outfile.flush()
            os.fsync(outfile.fileno())
        try:
            os.replace(temporary_path, path)
        except OSError as error:
            if error.errno != errno.EBUSY:
                raise
            # An individual Docker bind-mounted file cannot be renamed. Copy
            # the already serialized state into that file without changing its
            # inode. Directory-mounted state still takes the atomic path above.
            with open(temporary_path, "rb") as source, open(path, "r+b") as target:
                shutil.copyfileobj(source, target)
                target.truncate()
                target.flush()
                os.fsync(target.fileno())
    finally:
        if temporary_path is not None and os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return True


def save_tokens(tokens):
    return _save_json(_TOKENS_PATH, tokens)


def load_phones():
    """Returns {token: {"phone": ..., "title": ...}} for ads whose seller
    phone number we managed to retrieve. Kept separate from tokens.json and
    never sent to Telegram - for internal/admin lookup only."""
    try:
        with open(_PHONES_PATH, "r") as content:
            data = content.read()
            if not data:
                return {}
            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                print(
                    "Warning: phones.json did not contain a dict "
                    "(got {}), resetting to empty dict.".format(type(parsed))
                )
                return {}
            return parsed
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_phones(phones):
    return _save_json(_PHONES_PATH, phones, ensure_ascii=False, indent=2)
