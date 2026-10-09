"""Everything related to talking to Divar's (unofficial) web API and
parsing the responses into plain Python data."""
import datetime
import json

import requests
from pydantic import BaseModel, ValidationError

import config


DIVAR_TIMEOUT = (5, 20)


class DivarSearchError(RuntimeError):
    """The search failed or returned an unexpected response."""


class _InvalidPayload(ValueError):
    """A field needed by the ad parser has an unexpected JSON shape."""


def _object(value, field):
    if not isinstance(value, dict):
        raise _InvalidPayload("{} must be an object".format(field))
    return value


def _object_list(value, field):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise _InvalidPayload("{} must be a list of objects".format(field))
    return value


class AD(BaseModel):
    title: str
    price: int
    description: str = ""
    district: str
    images: list[str] = []
    token: str
    features: list[tuple[str, str]] = []  # e.g. [("متراژ ویلا", "۱۰۰"), ...]
    posted_in: str = ""  # e.g. "علی‌آباد کتول، خ ابر-شیرین آباد-علی آباد"
    breadcrumb_categories: list[str] = []  # e.g. ["اجارهٔ کوتاه‌مدت", ...]
    phone: str = ""  # seller's phone number, when we can find one


_debug_dumped_once = False


def build_search_body():
    """Builds the JSON body for Divar's postlist search API (first page only)."""
    return {
        "city_ids": config.SEARCH_CITY_IDS,
        "search_data": {
            "form_data": {
                "data": {"category": {"str": {"value": config.SEARCH_CATEGORY}}}
            },
            "server_payload": {
                "@type": "type.googleapis.com/widgets.SearchData.ServerPayload",
                "additional_form_data": {
                    "data": {"sort": {"str": {"value": "sort_date"}}}
                },
            },
        },
        "disable_recommendation": False,
        "map_state": {"camera_info": {"bbox": {}}},
        "current_tab_slug": "default",
    }


def get_data():
    body = build_search_body()
    try:
        response = requests.post(
            config.DIVAR_SEARCH_URL,
            headers=config.REQUEST_HEADERS,
            json=body,
            timeout=DIVAR_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise DivarSearchError(
            "Divar search request failed ({}).".format(type(error).__name__)
        ) from error
    print(
        "{} - Got response: {}".format(datetime.datetime.now(), response.status_code)
    )
    try:
        data = response.json()
    except ValueError as error:
        raise DivarSearchError("Divar search response was not valid JSON.") from error
    get_ads_list(data)
    return data


def get_ads_list(data):
    # Divar returns posts under "list_widgets"
    if not isinstance(data, dict) or not isinstance(data.get("list_widgets"), list):
        raise DivarSearchError(
            "Unexpected Divar search response: expected an object with a list_widgets list."
        )
    widgets = data["list_widgets"]
    for widget in widgets:
        if not isinstance(widget, dict):
            raise DivarSearchError("Unexpected Divar search response: widget must be an object.")
        if widget.get("widget_type") == "POST_ROW":
            row = widget.get("data")
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("token"), str)
                or not row["token"].strip()
            ):
                raise DivarSearchError("Unexpected Divar search response: post token is missing or invalid.")
    return widgets


def get_tokens_page():
    data = get_data()
    data = get_ads_list(data)
    print("Raw list_widgets length: {}".format(len(data)))
    data = data[::-1]
    # get tokens - only real post rows (skip banners, blocking views, etc.)
    data = filter(lambda x: x.get("widget_type") == "POST_ROW", data)
    tokens = list(map(lambda x: x["data"]["token"], data))
    return tokens


def extract_features(sections):
    """Walks the LIST_DATA section (and any nested amenities modal inside it)
    and returns a list of (label, value) pairs for every structured field
    Divar shows on the ad page (metrage, capacity, rent prices, amenities...).
    """
    features = []

    def process_widgets(widgets):
        pending_label = None
        for w in _object_list(widgets, "feature widgets"):
            wtype = w.get("widget_type")
            data = _object(w.get("data", {}), "widget data")

            if wtype == "GROUP_INFO_ROW":
                for item in _object_list(data.get("items", []), "feature items"):
                    t, v = item.get("title", ""), item.get("value", "")
                    if t and v:
                        features.append((t, v))

            elif wtype == "UNEXPANDABLE_ROW":
                t, v = data.get("title", ""), data.get("value", "")
                if t and v:
                    features.append((t, v))

            elif wtype == "DESCRIPTION_ROW":
                # usually a label for a following chip list (e.g. "چشم‌انداز")
                pending_label = data.get("text", "")

            elif wtype == "WRAPPER_ROW":
                chip_list = _object(data.get("chip_list", {}), "chip list")
                chips = _object_list(chip_list.get("chips", []), "chips")
                chip_texts = [c.get("text", "") for c in chips if c.get("text")]
                if chip_texts:
                    features.append((pending_label or "ویژگی", "، ".join(chip_texts)))
                pending_label = None

            elif wtype == "SELECTOR_ROW":
                # amenities are often tucked inside a modal opened by this row
                action = _object(data.get("action", {}), "selector action")
                payload = _object(action.get("payload", {}), "selector payload")
                modal = _object(payload.get("modal_page", {}), "selector modal")
                if "widget_list" in modal:
                    process_widgets(modal["widget_list"])

            # SECTION_TITLE_ROW and others are just headers/dividers -> skip

    for section in sections:
        if section.get("section_name") == "LIST_DATA":
            process_widgets(section.get("widgets", []))

    return features


def extract_posted_in(sections):
    """Pulls the '<n> هفته پیش در <location>' line shown under the title."""
    for section in sections:
        if section.get("section_name") == "TITLE":
            for w in section.get("widgets", []):
                if w.get("widget_type") == "EXPANDABLE_SECTION":
                    return _object(w.get("data", {}), "widget data").get("title", "")
    return ""


def extract_breadcrumb_categories(sections):
    """Pulls the category chain shown in the BREADCRUMB section, e.g.
    ["املاک", "اجارهٔ کوتاه‌مدت", "اجارهٔ کوتاه‌مدت ویلا و باغ"]."""
    titles = []
    for section in sections:
        if section.get("section_name") == "BREADCRUMB":
            for w in section.get("widgets", []):
                if w.get("widget_type") == "BREADCRUMB":
                    data = _object(w.get("data", {}), "widget data")
                    for item in _object_list(data.get("parent_items", []), "breadcrumb items"):
                        t = item.get("title")
                        if t:
                            titles.append(t)
    return titles


def extract_phone_from_sections(sections):
    """Some ads (mostly agencies) show the phone number directly as a row
    on the post page, without needing the "show number" button. Walks the
    whole widget tree looking for a tel: link or a title/value row whose
    label mentions "شماره" (number)."""

    def walk(node):
        if isinstance(node, dict):
            url = node.get("url") or node.get("phone_number")
            if isinstance(url, str) and url.startswith("tel:"):
                return url[len("tel:") :].strip()

            title = node.get("title", "")
            value = node.get("value", "")
            if isinstance(title, str) and "شماره" in title and isinstance(value, str) and value:
                digits = "".join(c for c in value if c.isdigit())
                if len(digits) >= 10:
                    return value

            for v in node.values():
                found = walk(v)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return None

    return walk(sections) or ""


def fetch_contact_phone(token: str) -> str:
    """Calls Divar's separate contact-info endpoint (the one behind the
    "show phone number" button) to fetch the seller's number. Best-effort:
    Divar doesn't document this endpoint, so we fail quietly if the shape
    doesn't match what we expect or the request is rejected."""
    try:
        response = requests.post(
            config.DIVAR_CONTACT_URL.format(token=token),
            headers=config.REQUEST_HEADERS,
            json={"token": token},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        print("Warning: couldn't fetch phone for {} ({}).".format(token, type(error).__name__))
        return ""
    try:
        data = response.json()
    except ValueError:
        print("Warning: contact response for {} was not valid JSON.".format(token))
        return ""
    if not isinstance(data, dict):
        print("Warning: contact response for {} was not an object.".format(token))
        return ""

    for key in ("phone_number", "phone", "contact_phone", "mobile"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    # some responses nest it, e.g. {"widget": {"data": {"phone_number": ...}}}
    return extract_phone_from_sections(data)


def fetch_ad_data(token: str) -> AD | None:
    try:
        response = requests.get(
            config.DIVAR_POST_DETAIL_URL.format(token=token),
            headers=config.REQUEST_HEADERS,
            timeout=DIVAR_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        print("Warning: couldn't fetch ad {} ({}), skipping.".format(token, type(error).__name__))
        return None
    try:
        data = response.json()
    except ValueError:
        print("Warning: response for ad {} was not valid JSON, skipping.".format(token))
        return None

    try:
        data = _object(data, "ad response")
        sections = _object_list(data.get("sections"), "sections")
        if not sections:
            raise _InvalidPayload("sections must not be empty")
        for section in sections:
            if not isinstance(section.get("section_name"), str):
                raise _InvalidPayload("section name must be a string")
            for widget in _object_list(section.get("widgets", []), "section widgets"):
                _object(widget.get("data", {}), "widget data")
    except _InvalidPayload:
        print("Warning: unexpected response structure for ad {}, skipping.".format(token))
        return None

    if config.DEBUG_DUMP_SECTIONS:
        global _debug_dumped_once
        if not _debug_dumped_once:
            _debug_dumped_once = True
            print("===== FULL SECTIONS DUMP for token {} =====".format(token))
            print(json.dumps(sections, ensure_ascii=False))
            print("===== END DUMP =====")

    title = ""
    description = ""
    images = []

    try:
        for section in sections:
            if section["section_name"] == "TITLE":
                title = section["widgets"][0]["data"]["title"]

            if section["section_name"] == "IMAGE":
                images = section["widgets"][0]["data"]["items"]
                images = [
                    _object(img.get("image"), "image")["url"]
                    for img in _object_list(images, "images")
                ]

            if section["section_name"] == "DESCRIPTION":
                description = section["widgets"][1]["data"]["text"]

        if not isinstance(title, str) or not title.strip():
            raise _InvalidPayload("ad title is missing or invalid")

        seo = _object(data.get("seo", {}), "SEO")
        web_info = _object(seo.get("web_info", {}), "web info")
        district = web_info.get("district_persian", "")
        webengage = _object(data.get("webengage", {}), "webengage")
        price = webengage.get("price", 0) or 0

        features = extract_features(sections)
        posted_in = extract_posted_in(sections)
        breadcrumb_categories = extract_breadcrumb_categories(sections)

        phone = extract_phone_from_sections(sections)
        if not phone:
            phone = fetch_contact_phone(token)

        ad = AD(
            token=token,
            title=title,
            district=district,
            description=description,
            images=images,
            price=price,
            features=features,
            posted_in=posted_in,
            breadcrumb_categories=breadcrumb_categories,
            phone=phone,
        )
    except (KeyError, IndexError, TypeError, _InvalidPayload, ValidationError) as error:
        print("Warning: failed to parse ad {} ({}), skipping.".format(token, type(error).__name__))
        return None

    return ad
