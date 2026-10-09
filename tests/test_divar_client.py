"""Divar response regressions using synthetic configuration and mocked Requests."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import requests


def load_client():
    config = types.ModuleType("config")
    config.DIVAR_SEARCH_URL = "https://example.invalid/search"
    config.DIVAR_POST_DETAIL_URL = "https://example.invalid/posts/{token}"
    config.DIVAR_CONTACT_URL = "https://example.invalid/contact/{token}"
    config.SEARCH_CITY_IDS = ["823", "1996"]
    config.SEARCH_CATEGORY = "real-estate"
    config.REQUEST_HEADERS = {"accept": "application/json"}
    config.DEBUG_DUMP_SECTIONS = False
    path = Path(__file__).resolve().parents[1] / "divar_client.py"
    spec = importlib.util.spec_from_file_location("divar_client_test_target", path)
    module = importlib.util.module_from_spec(spec)
    missing = object()
    replacements = {"config": config, spec.name: module}
    previous = {name: sys.modules.get(name, missing) for name in replacements}
    sys.modules.update(replacements)
    try:
        spec.loader.exec_module(module)
    finally:
        for name, original in previous.items():
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return module


client = load_client()


def detail_payload():
    return {
        "sections": [
            {"section_name": "TITLE", "widgets": [
                {"data": {"title": "فروش ویلا"}},
                {"widget_type": "EXPANDABLE_SECTION", "data": {"title": "علی آباد کتول"}},
            ]},
            {"section_name": "IMAGE", "widgets": [{"data": {"items": [
                {"image": {"url": "https://example.invalid/photo.jpg"}}
            ]}}]},
            {"section_name": "DESCRIPTION", "widgets": [{"data": {}}, {"data": {"text": "توضیح نمونه"}}]},
            {"section_name": "LIST_DATA", "widgets": [
                {"widget_type": "GROUP_INFO_ROW", "data": {"items": [{"title": "متراژ", "value": "۱۰۰"}]}},
                {"widget_type": "DESCRIPTION_ROW", "data": {"text": "چشم‌انداز"}},
                {"widget_type": "WRAPPER_ROW", "data": {"chip_list": {"chips": [{"text": "جنگل"}, {"text": "کوه"}]}}},
                {"widget_type": "SELECTOR_ROW", "data": {"action": {"payload": {"modal_page": {"widget_list": [
                    {"widget_type": "UNEXPANDABLE_ROW", "data": {"title": "پارکینگ", "value": "دارد"}}
                ]}}}}},
            ]},
            {"section_name": "BREADCRUMB", "widgets": [{"widget_type": "BREADCRUMB", "data": {"parent_items": [{"title": "املاک"}]}}]},
            {"section_name": "CONTACT", "widgets": [{"data": {"url": "tel:00000000000"}}]},
        ],
        "seo": {"web_info": {"district_persian": "نمونه"}},
        "webengage": {"price": "2500000"},
    }


def response(data):
    result = Mock()
    result.status_code = 200
    result.json.return_value = data
    return result


class DivarClientTests(unittest.TestCase):
    def setUp(self):
        self.get = self.enterContext(patch.object(client.requests, "get"))
        self.post = self.enterContext(patch.object(client.requests, "post"))
        self.get.side_effect = AssertionError("Unexpected GET request")
        self.post.side_effect = AssertionError("Unexpected POST request")
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))

    def stub_get(self, data):
        self.get.side_effect = None
        self.get.return_value = response(data)
        return self.get.return_value

    def stub_post(self, data):
        self.post.side_effect = None
        self.post.return_value = response(data)
        return self.post.return_value

    def test_search_preserves_body_and_order_and_has_timeout(self):
        reply = self.stub_post({"list_widgets": [
            {"widget_type": "POST_ROW", "data": {"token": "newer"}},
            {"widget_type": "BANNER", "data": {}},
            {"widget_type": "POST_ROW", "data": {"token": "older"}},
        ]})
        self.assertEqual(client.get_tokens_page(), ["older", "newer"])
        self.post.assert_called_once_with(
            client.config.DIVAR_SEARCH_URL,
            headers=client.config.REQUEST_HEADERS,
            json=client.build_search_body(),
            timeout=(5, 20),
        )
        body = self.post.call_args.kwargs["json"]
        self.assertEqual(body["city_ids"], ["823", "1996"])
        self.assertEqual(body["search_data"]["form_data"]["data"]["category"]["str"]["value"], "real-estate")
        self.assertEqual(body["search_data"]["server_payload"]["additional_form_data"]["data"]["sort"]["str"]["value"], "sort_date")
        reply.raise_for_status.assert_called_once_with()

    def test_empty_search_page_is_valid(self):
        self.stub_post({"list_widgets": []})
        self.assertEqual(client.get_tokens_page(), [])

    def test_search_request_failures_raise_with_context(self):
        for error in (requests.Timeout("private error"), requests.ConnectionError("private error")):
            with self.subTest(error=type(error).__name__):
                self.post.side_effect = error
                with self.assertRaisesRegex(client.DivarSearchError, "Divar search request failed") as caught:
                    client.get_data()
                self.assertIs(caught.exception.__cause__, error)
                self.assertNotIn("private error", str(caught.exception))

    def test_search_http_error_is_checked_before_json(self):
        reply = self.stub_post({"list_widgets": []})
        reply.raise_for_status.side_effect = requests.HTTPError("503")
        with self.assertRaises(client.DivarSearchError):
            client.get_data()
        reply.json.assert_not_called()

    def test_search_invalid_json_raises_with_context(self):
        reply = self.stub_post(None)
        reply.json.side_effect = requests.exceptions.JSONDecodeError("invalid", "private body", 0)
        with self.assertRaisesRegex(client.DivarSearchError, "not valid JSON") as caught:
            client.get_data()
        self.assertNotIn("private body", str(caught.exception))

    def test_unexpected_search_shapes_are_not_treated_as_empty_pages(self):
        for data in (
            None, [], {}, {"list_widgets": None}, {"list_widgets": {}},
            {"list_widgets": [None]},
            {"list_widgets": [{"widget_type": "POST_ROW"}]},
            {"list_widgets": [{"widget_type": "POST_ROW", "data": {"token": 123}}]},
            {"list_widgets": [{"widget_type": "POST_ROW", "data": {"token": " "}}]},
        ):
            with self.subTest(data=data):
                self.stub_post(data)
                with self.assertRaisesRegex(client.DivarSearchError, "Unexpected Divar search response"):
                    client.get_tokens_page()

    def test_detail_parsing_preserves_fields_features_and_phone(self):
        reply = self.stub_get(detail_payload())
        ad = client.fetch_ad_data("sample-ad")
        self.assertIsInstance(ad, client.AD)
        self.assertEqual(ad.token, "sample-ad")
        self.assertEqual(ad.title, "فروش ویلا")
        self.assertEqual(ad.price, 2500000)
        self.assertEqual(ad.description, "توضیح نمونه")
        self.assertEqual(ad.district, "نمونه")
        self.assertEqual(ad.images, ["https://example.invalid/photo.jpg"])
        self.assertEqual(ad.features, [("متراژ", "۱۰۰"), ("چشم‌انداز", "جنگل، کوه"), ("پارکینگ", "دارد")])
        self.assertEqual(ad.posted_in, "علی آباد کتول")
        self.assertEqual(ad.breadcrumb_categories, ["املاک"])
        self.assertEqual(ad.phone, "00000000000")
        self.get.assert_called_once_with(
            client.config.DIVAR_POST_DETAIL_URL.format(token="sample-ad"),
            headers=client.config.REQUEST_HEADERS,
            timeout=(5, 20),
        )
        reply.raise_for_status.assert_called_once_with()
        self.post.assert_not_called()

    def test_detail_network_failures_skip_ad_and_do_not_log_exception_values(self):
        for error in (requests.Timeout("private error"), requests.ConnectionError("private error")):
            with self.subTest(error=type(error).__name__):
                self.get.side_effect = error
                self.assertIsNone(client.fetch_ad_data("sample-ad"))
        self.assertNotIn("private error", self.output.getvalue())

    def test_detail_http_failure_skips_ad_before_parsing_json(self):
        for status in (404, 503):
            with self.subTest(status=status):
                reply = self.stub_get({"sections": []})
                reply.raise_for_status.side_effect = requests.HTTPError(str(status))
                self.assertIsNone(client.fetch_ad_data("sample-ad"))
                reply.json.assert_not_called()

    def test_detail_invalid_json_skips_ad_without_logging_response_body(self):
        reply = self.stub_get(None)
        reply.json.side_effect = requests.exceptions.JSONDecodeError("invalid", "private response", 0)
        self.assertIsNone(client.fetch_ad_data("sample-ad"))
        self.assertNotIn("private response", self.output.getvalue())

    def test_detail_rejects_unexpected_payload_and_section_shapes(self):
        for data in (
            None, [], {}, {"sections": None}, {"sections": {}}, {"sections": []},
            {"sections": [None]}, {"sections": [{}]},
            {"sections": [{"section_name": "TITLE", "widgets": {}}]},
            {"sections": [{"section_name": "TITLE", "widgets": [None]}]},
            {"sections": [{"section_name": "TITLE", "widgets": [{"data": []}]}]},
        ):
            with self.subTest(data=data):
                self.stub_get(data)
                self.assertIsNone(client.fetch_ad_data("sample-ad"))

    def test_detail_rejects_malformed_nested_fields(self):
        bad_sections = [
            {"section_name": "LIST_DATA", "widgets": [{"widget_type": "GROUP_INFO_ROW", "data": {"items": [None]}}]},
            {"section_name": "LIST_DATA", "widgets": [{"widget_type": "WRAPPER_ROW", "data": {"chip_list": []}}]},
            {"section_name": "LIST_DATA", "widgets": [{"widget_type": "SELECTOR_ROW", "data": {"action": {"payload": None}}}]},
            {"section_name": "LIST_DATA", "widgets": [{"widget_type": "SELECTOR_ROW", "data": {"action": {"payload": {"modal_page": {"widget_list": {}}}}}}]},
            {"section_name": "BREADCRUMB", "widgets": [{"widget_type": "BREADCRUMB", "data": {"parent_items": [None]}}]},
            {"section_name": "IMAGE", "widgets": [{"data": {"items": [{"image": []}]}}]},
            {"section_name": "DESCRIPTION", "widgets": []},
        ]
        for section in bad_sections:
            with self.subTest(section=section):
                data = detail_payload()
                data["sections"].append(section)
                self.stub_get(data)
                self.assertIsNone(client.fetch_ad_data("sample-ad"))
        for field, value in (("seo", []), ("webengage", None)):
            with self.subTest(field=field):
                data = detail_payload()
                data[field] = value
                self.stub_get(data)
                self.assertIsNone(client.fetch_ad_data("sample-ad"))

    def test_detail_without_a_valid_title_is_not_sent_as_an_empty_ad(self):
        for title in (None, "", " "):
            with self.subTest(title=title):
                data = detail_payload()
                data["sections"][0]["widgets"][0]["data"]["title"] = title
                self.stub_get(data)
                self.assertIsNone(client.fetch_ad_data("sample-ad"))
        self.stub_get({"sections": [{"section_name": "UNKNOWN", "widgets": []}]})
        self.assertIsNone(client.fetch_ad_data("sample-ad"))
        self.post.assert_not_called()

    def test_pydantic_validation_failure_skips_ad_without_logging_values(self):
        data = detail_payload()
        data["webengage"]["price"] = "private-invalid-price"
        self.stub_get(data)
        self.assertIsNone(client.fetch_ad_data("sample-ad"))
        self.assertIn("ValidationError", self.output.getvalue())
        self.assertNotIn("private-invalid-price", self.output.getvalue())

    def test_contact_fallback_retains_request_semantics(self):
        data = detail_payload()
        data["sections"] = data["sections"][:-1]
        self.stub_get(data)
        reply = self.stub_post({"phone_number": " 00000000000 "})
        self.assertEqual(client.fetch_ad_data("sample-ad").phone, "00000000000")
        self.post.assert_called_once_with(
            client.config.DIVAR_CONTACT_URL.format(token="sample-ad"),
            headers=client.config.REQUEST_HEADERS,
            json={"token": "sample-ad"},
            timeout=15,
        )
        reply.raise_for_status.assert_called_once_with()

    def test_contact_failures_are_best_effort(self):
        for data in (None, [], "invalid"):
            with self.subTest(data=data):
                self.stub_post(data)
                self.assertEqual(client.fetch_contact_phone("sample-ad"), "")
        reply = self.stub_post({})
        reply.raise_for_status.side_effect = requests.HTTPError("private error")
        self.assertEqual(client.fetch_contact_phone("sample-ad"), "")
        reply.json.assert_not_called()
        reply = self.stub_post({})
        reply.json.side_effect = ValueError("private error")
        self.assertEqual(client.fetch_contact_phone("sample-ad"), "")
        self.post.side_effect = requests.Timeout("private error")
        self.assertEqual(client.fetch_contact_phone("sample-ad"), "")
        self.assertNotIn("private error", self.output.getvalue())

    def test_phone_walker_skips_non_string_number_values(self):
        self.assertEqual(client.extract_phone_from_sections({"title": "شماره تماس", "value": 12345}), "")
        self.assertEqual(client.extract_phone_from_sections({"nested": {"url": "tel:00000000000"}}), "00000000000")

    def test_unexpected_programming_errors_are_not_swallowed(self):
        self.get.side_effect = RuntimeError("programming error")
        with self.assertRaisesRegex(RuntimeError, "programming error"):
            client.fetch_ad_data("sample-ad")
        reply = self.stub_get(detail_payload())
        reply.json.side_effect = RuntimeError("programming error")
        with self.assertRaisesRegex(RuntimeError, "programming error"):
            client.fetch_ad_data("sample-ad")
        self.stub_get(detail_payload())
        with patch.object(client, "extract_features", side_effect=RuntimeError("programming error")):
            with self.assertRaisesRegex(RuntimeError, "programming error"):
                client.fetch_ad_data("sample-ad")


if __name__ == "__main__":
    unittest.main()
