"""Delivery regressions, isolated from credentials, real state, and networking."""
import asyncio
from contextlib import ExitStack, redirect_stdout
import importlib
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import requests
import telegram


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(
            patch.dict(os.environ, {"BOT_TOKEN": "offline-test", "BOT_CHATID": "offline-chat"}, clear=True)
        )
        self.stack.enter_context(
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("live HTTP forbidden"))
        )
        self.stack.enter_context(
            patch.object(socket, "create_connection", side_effect=AssertionError("live sockets forbidden"))
        )
        self.bot = SimpleNamespace(
            send_photo=AsyncMock(), send_media_group=AsyncMock(), send_message=AsyncMock()
        )
        self.stack.enter_context(patch.object(telegram, "Bot", return_value=self.bot))
        self.stack.enter_context(patch.object(telegram.request, "HTTPXRequest", return_value=object()))

        # Other tests may already have imported config with their own fixtures.
        # Fresh imports here ensure the application never sees real credentials.
        names = ("config", "main", "divar_client", "telegram_client")
        previous = {name: sys.modules.pop(name) for name in names if name in sys.modules}

        def restore_modules():
            for name in names:
                sys.modules.pop(name, None)
            sys.modules.update(previous)

        self.stack.callback(restore_modules)
        self.storage = importlib.import_module("storage")
        self.stack.enter_context(patch.object(self.storage, "_TOKENS_PATH", os.path.join(self.temp, "tokens.json")))
        self.stack.enter_context(patch.object(self.storage, "_PHONES_PATH", os.path.join(self.temp, "phones.json")))
        self.main = importlib.import_module("main")
        self.client = importlib.import_module("telegram_client")
        self.AD = importlib.import_module("divar_client").AD
        self.stack.enter_context(patch("time.sleep"))
        self.stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))

    def ad(self, token="new", phone=""):
        return self.AD(token=token, title="Fixture title", price=1, district="Fixture district", phone=phone)

    def state_bytes(self):
        return tuple(Path(path).read_bytes() if Path(path).exists() else None for path in (
            self.storage._TOKENS_PATH, self.storage._PHONES_PATH
        ))

    def seed(self, tokens, phones=None):
        self.storage.save_tokens(tokens)
        self.storage.save_phones(phones or {})

    def test_callback_and_return_only_report_acknowledged_delivery(self):
        delivered_ad = self.ad("delivered", phone="fixture-phone")
        failed_ad = self.ad("failed", phone="failed-phone")
        phones = {}
        callbacks = []

        def acknowledge(token):
            callbacks.append((token, dict(phones)))

        send = AsyncMock(side_effect=[None, telegram.error.Forbidden("blocked")])
        with patch.object(self.main, "fetch_ad_data", side_effect=[delivered_ad, failed_ad]), patch.object(
            self.main, "send_telegram_message", send
        ):
            result = asyncio.run(self.main.process_data(["delivered", "failed"], phones, acknowledge))

        expected_phones = {"delivered": {"phone": "fixture-phone", "title": delivered_ad.title}}
        self.assertEqual(result, ["delivered"])
        self.assertEqual(phones, expected_phones)
        self.assertEqual(callbacks, [("delivered", expected_phones)])

    def test_unparsed_ad_is_retried_on_next_run(self):
        self.seed(["existing"])
        before = self.state_bytes()
        ad = self.ad()
        send = AsyncMock()
        with patch.object(self.main, "get_tokens_page", return_value=["new"]), patch.object(
            self.main, "fetch_ad_data", side_effect=[None, ad]
        ) as fetch, patch.object(self.main, "send_telegram_message", send):
            self.main.main()
            self.assertEqual(self.state_bytes(), before)
            self.main.main()

        self.assertEqual(fetch.call_count, 2)
        send.assert_awaited_once_with(ad)
        self.assertEqual(self.storage.load_tokens(), ["existing", "new"])

    def test_two_timeouts_leave_ad_and_phone_unsaved_then_retry_next_run(self):
        self.seed(["existing"], {"existing": {"phone": "old", "title": "Old"}})
        before = self.state_bytes()
        ad = self.ad(phone="fixture-phone")
        send = AsyncMock(side_effect=[telegram.error.TimedOut(), telegram.error.TimedOut(), None])
        with patch.object(self.main, "get_tokens_page", return_value=["new"]), patch.object(
            self.main, "fetch_ad_data", return_value=ad
        ), patch.object(self.main, "send_telegram_message", send):
            self.main.main()
            self.assertEqual(send.await_count, 2)
            self.assertEqual(self.state_bytes(), before)
            self.main.main()

        self.assertEqual(send.await_count, 3)
        self.assertEqual(self.storage.load_tokens(), ["existing", "new"])
        self.assertEqual(self.storage.load_phones()["new"]["phone"], "fixture-phone")

    def test_one_timeout_then_success_acknowledges_once(self):
        ad = self.ad(phone="fixture-phone")
        calls = []
        send = AsyncMock(side_effect=[telegram.error.TimedOut(), None])
        with patch.object(self.main, "fetch_ad_data", return_value=ad), patch.object(
            self.main, "send_telegram_message", send
        ):
            delivered = asyncio.run(self.main.process_data(["new"], {}, calls.append))
        self.assertEqual(send.await_count, 2)
        self.assertEqual(delivered, ["new"])
        self.assertEqual(calls, ["new"])

    def test_success_is_not_resent_and_second_run_does_not_write_state(self):
        self.seed(["existing"])
        ad = self.ad(phone="fixture-phone")
        send = AsyncMock()
        with patch.object(self.main, "get_tokens_page", return_value=["new", "existing"]), patch.object(
            self.main, "fetch_ad_data", return_value=ad
        ) as fetch, patch.object(self.main, "send_telegram_message", send):
            self.main.main()
            saved = self.state_bytes()
            with patch.object(self.main, "save_tokens", wraps=self.storage.save_tokens) as save_tokens, patch.object(
                self.main, "save_phones", wraps=self.storage.save_phones
            ) as save_phones:
                self.main.main()
            save_tokens.assert_not_called()
            save_phones.assert_not_called()
            self.assertEqual(self.state_bytes(), saved)
        fetch.assert_called_once_with("new")
        send.assert_awaited_once_with(ad)

    def test_duplicate_search_results_send_once_and_preserve_seen_order(self):
        self.seed(["z-existing", "a-existing"])
        send = AsyncMock()
        with patch.object(
            self.main, "get_tokens_page", return_value=["a-existing", "b-new", "b-new", "c-new", "z-existing"]
        ), patch.object(self.main, "fetch_ad_data", side_effect=lambda token: self.ad(token)) as fetch, patch.object(
            self.main, "send_telegram_message", send
        ):
            self.main.main()
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["b-new", "c-new"])
        self.assertEqual(send.await_count, 2)
        self.assertEqual(self.storage.load_tokens(), ["z-existing", "a-existing", "b-new", "c-new"])

    def test_telegram_error_does_not_block_next_ad_or_mark_failure_seen(self):
        self.seed(["existing"])
        send = AsyncMock(side_effect=[telegram.error.TelegramError("failure"), None])
        with patch.object(self.main, "get_tokens_page", return_value=["failed", "delivered"]), patch.object(
            self.main, "fetch_ad_data", side_effect=lambda token: self.ad(token, phone="fixture-phone")
        ), patch.object(self.main, "send_telegram_message", send):
            self.main.main()
        self.assertEqual(send.await_count, 2)
        self.assertEqual(self.storage.load_tokens(), ["existing", "delivered"])
        self.assertNotIn("failed", self.storage.load_phones())

    def test_album_bad_request_propagates_from_telegram_client(self):
        ad = self.ad()
        ad.images = ["https://example.invalid/1.jpg", "https://example.invalid/2.jpg"]
        self.bot.send_media_group.side_effect = telegram.error.BadRequest("fixture album rejected")
        with self.assertRaises(telegram.error.BadRequest):
            asyncio.run(self.client.send_telegram_message(ad))
        self.bot.send_media_group.assert_awaited_once()

    def test_rejected_album_stays_unseen_while_next_ad_is_delivered(self):
        self.seed(["existing"])
        album = self.ad("album", phone="fixture-phone")
        album.images = ["https://example.invalid/1.jpg", "https://example.invalid/2.jpg"]
        good = self.ad("good")
        self.bot.send_media_group.side_effect = telegram.error.BadRequest("fixture album rejected")
        with patch.object(self.main, "get_tokens_page", return_value=["album", "good"]), patch.object(
            self.main, "fetch_ad_data", side_effect=[album, good]
        ):
            self.main.main()
        self.bot.send_media_group.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.storage.load_tokens(), ["existing", "good"])
        self.assertNotIn("album", self.storage.load_phones())

    def test_prior_success_survives_unexpected_failure_and_is_not_resent(self):
        self.seed(["existing"])
        first = self.ad("first", phone="fixture-phone")
        second = self.ad("second")
        send = AsyncMock()
        with patch.object(self.main, "get_tokens_page", return_value=["first", "second"]), patch.object(
            self.main, "fetch_ad_data", side_effect=[first, RuntimeError("fixture failure"), second]
        ) as fetch, patch.object(self.main, "send_telegram_message", send):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                self.main.main()
            self.assertEqual(self.storage.load_tokens(), ["existing", "first"])
            self.assertEqual(self.storage.load_phones(), {"first": {"phone": "fixture-phone", "title": first.title}})
            self.main.main()
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["first", "second", "second"])
        self.assertEqual([call.args[0].token for call in send.await_args_list], ["first", "second"])
        self.assertEqual(self.storage.load_tokens(), ["existing", "first", "second"])

    def test_no_successful_ads_does_not_create_state_files(self):
        with patch.object(self.main, "get_tokens_page", return_value=["unparsed"]), patch.object(
            self.main, "fetch_ad_data", return_value=None
        ), patch.object(self.main, "save_tokens", wraps=self.storage.save_tokens) as save_tokens, patch.object(
            self.main, "save_phones", wraps=self.storage.save_phones
        ) as save_phones:
            self.main.main()
        save_tokens.assert_not_called()
        save_phones.assert_not_called()
        self.assertEqual(self.state_bytes(), (None, None))

    def test_prior_success_survives_unexpected_send_failure_and_stops_further_sends(self):
        self.seed(["existing"])
        send = AsyncMock(side_effect=[None, RuntimeError("fixture send failure"), None, None])
        with patch.object(self.main, "get_tokens_page", return_value=["first", "second", "third"]), patch.object(
            self.main, "fetch_ad_data", side_effect=lambda token: self.ad(token, phone="fixture-phone")
        ) as fetch, patch.object(self.main, "send_telegram_message", send):
            with self.assertRaisesRegex(RuntimeError, "fixture send failure"):
                self.main.main()
            self.assertEqual([call.args[0] for call in fetch.call_args_list], ["first", "second"])
            self.assertEqual([call.args[0].token for call in send.await_args_list], ["first", "second"])
            self.assertEqual(self.storage.load_tokens(), ["existing", "first"])
            self.assertEqual(set(self.storage.load_phones()), {"first"})
            self.main.main()
        self.assertEqual([call.args[0].token for call in send.await_args_list], ["first", "second", "second", "third"])
        self.assertEqual(self.storage.load_tokens(), ["existing", "first", "second", "third"])

    def test_phone_save_failure_stops_delivery_and_preserves_acknowledged_token(self):
        self.seed(["existing"], {"existing": {"phone": "old", "title": "Old"}})
        before_phones = Path(self.storage._PHONES_PATH).read_bytes()
        send = AsyncMock()
        with patch.object(self.main, "get_tokens_page", return_value=["first", "second"]), patch.object(
            self.main, "fetch_ad_data", side_effect=lambda token: self.ad(token, phone="fixture-phone")
        ) as fetch, patch.object(self.main, "send_telegram_message", send):
            with patch.object(self.main, "save_phones", side_effect=OSError("fixture phone save failure")):
                with self.assertRaisesRegex(OSError, "fixture phone save failure"):
                    self.main.main()
            self.assertEqual([call.args[0] for call in fetch.call_args_list], ["first"])
            self.assertEqual([call.args[0].token for call in send.await_args_list], ["first"])
            self.assertEqual(self.storage.load_tokens(), ["existing", "first"])
            self.assertEqual(Path(self.storage._PHONES_PATH).read_bytes(), before_phones)
            self.main.main()
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["first", "second"])
        self.assertEqual([call.args[0].token for call in send.await_args_list], ["first", "second"])
        self.assertEqual(self.storage.load_tokens(), ["existing", "first", "second"])


if __name__ == "__main__":
    unittest.main()
