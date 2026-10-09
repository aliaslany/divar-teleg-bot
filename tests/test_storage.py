"""Storage regressions use only temporary paths, never checkout state."""
from contextlib import ExitStack
import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.tokens_path = Path(temp, "tokens.json")
        self.phones_path = Path(temp, "phones.json")
        self.stack.enter_context(patch.object(storage, "_TOKENS_PATH", str(self.tokens_path)))
        self.stack.enter_context(patch.object(storage, "_PHONES_PATH", str(self.phones_path)))

    def test_unchanged_tokens_preserve_existing_bytes_and_modification_time(self):
        self.tokens_path.write_text('[\n  "older",\n  "newer"\n]\n', encoding="utf-8")
        os.utime(self.tokens_path, ns=(1_000_000_000, 1_000_000_000))
        original = self.tokens_path.read_bytes()
        before = self.tokens_path.stat()
        storage.save_tokens(["older", "newer"])
        after = self.tokens_path.stat()
        self.assertEqual(self.tokens_path.read_bytes(), original)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))

    def test_equivalent_phones_preserve_bytes_despite_dictionary_order(self):
        original = '{"first": {"phone": "fixture-1", "title": "One"}, "second": {"phone": "fixture-2", "title": "Two"}}\n'
        self.phones_path.write_text(original, encoding="utf-8")
        os.utime(self.phones_path, ns=(1_000_000_000, 1_000_000_000))
        before = self.phones_path.stat()
        storage.save_phones({"second": {"title": "Two", "phone": "fixture-2"}, "first": {"title": "One", "phone": "fixture-1"}})
        after = self.phones_path.stat()
        self.assertEqual(self.phones_path.read_text(encoding="utf-8"), original)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))

    def test_changed_tokens_are_replaced_with_complete_ordered_json(self):
        storage.save_tokens(["first"])
        prior_inode = self.tokens_path.stat().st_ino
        storage.save_tokens(["first", "second"])
        self.assertEqual(json.loads(self.tokens_path.read_text(encoding="utf-8")), ["first", "second"])
        self.assertNotEqual(self.tokens_path.stat().st_ino, prior_inode)

    def test_changed_phones_are_replaced_with_complete_unicode_json(self):
        storage.save_phones({"first": {"phone": "fixture", "title": "Old"}})
        prior_inode = self.phones_path.stat().st_ino
        value = {"first": {"phone": "fixture", "title": "عنوان جدید"}}
        storage.save_phones(value)
        self.assertEqual(json.loads(self.phones_path.read_text(encoding="utf-8")), value)
        self.assertNotEqual(self.phones_path.stat().st_ino, prior_inode)

    def test_failed_serialization_preserves_previous_state_files(self):
        for saver, path, original, invalid in (
            (storage.save_tokens, self.tokens_path, ["old"], ["new", object()]),
            (storage.save_phones, self.phones_path, {"old": {"phone": "fixture"}}, {"new": object()}),
        ):
            with self.subTest(path=path.name):
                saver(original)
                before = path.read_bytes()
                with self.assertRaises(TypeError):
                    saver(invalid)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(set(path.parent.iterdir()), {p for p in (self.tokens_path, self.phones_path) if p.exists()})

    def test_failed_atomic_replace_preserves_previous_files_and_removes_temporary_files(self):
        for saver, path, original, changed in (
            (storage.save_tokens, self.tokens_path, ["old"], ["old", "new"]),
            (storage.save_phones, self.phones_path, {}, {"new": {"phone": "fixture"}}),
        ):
            with self.subTest(path=path.name):
                saver(original)
                before = path.read_bytes()
                prior_files = set(path.parent.iterdir())
                with patch.object(storage.os, "replace", side_effect=OSError("fixture replace failure")):
                    with self.assertRaisesRegex(OSError, "fixture replace failure"):
                        saver(changed)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(set(path.parent.iterdir()), prior_files)

    def test_busy_bind_mounted_files_are_updated_without_replacing_the_inode(self):
        for saver, path, original, changed in (
            (storage.save_tokens, self.tokens_path, ["old-" + "x" * 1_000], ["new"]),
            (storage.save_phones, self.phones_path, {"old": {"phone": "fixture", "title": "x" * 1_000}},
             {"new": {"phone": "fixture", "title": "عنوان"}}),
        ):
            with self.subTest(path=path.name):
                saver(original)
                before = path.stat()
                prior_files = set(path.parent.iterdir())
                with patch.object(storage.os, "replace", side_effect=OSError(errno.EBUSY, "fixture bind mount")) as replace:
                    saver(changed)
                    after = path.stat()
                    self.assertEqual(after.st_ino, before.st_ino)
                    self.assertLess(after.st_size, before.st_size)
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8")), changed)
                    self.assertEqual(set(path.parent.iterdir()), prior_files)
                    saved_bytes = path.read_bytes()
                    # An unchanged mounted file must avoid both replace and the
                    # in-place fallback, preserving exact bytes and mtime.
                    saver(changed)
                    replace.assert_called_once()
                    self.assertEqual(path.read_bytes(), saved_bytes)
                    self.assertEqual(path.stat().st_mtime_ns, after.st_mtime_ns)

    def test_same_tokens_in_different_order_are_a_real_change(self):
        storage.save_tokens(["first", "second"])
        storage.save_tokens(["second", "first"])
        self.assertEqual(storage.load_tokens(), ["second", "first"])


if __name__ == "__main__":
    unittest.main()
