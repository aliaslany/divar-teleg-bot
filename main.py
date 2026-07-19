import datetime
import html
import json
import os
import time

import requests
import telegram
from pydantic import BaseModel
import asyncio


URL = "https://api.divar.ir/v8/postlist/w/search"

BOT_TOKEN = "{BOT_TOKEN}".format(**os.environ)
BOT_CHATID = "{BOT_CHATID}".format(**os.environ)
SLEEP_SEC = "{SLEEP_SEC}".format(**os.environ)

# New search parameters (replace the old SEARCH_CONDITIONS URL-path string)
# Comma-separated list of city IDs, e.g. "823,1996,1999"
SEARCH_CITY_IDS = [
    c.strip()
    for c in os.environ.get("SEARCH_CITY_IDS", "897").split(",")
    if c.strip()
]
SEARCH_CATEGORY = os.environ.get("SEARCH_CATEGORY", "real-estate")

REQUEST_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "referrer": "https://divar.ir/",
    "x-render-type": "CSR",
    "x-standard-divar-error": "true",
}

proxy_url = None
if os.environ.get("PROXY_URL", ""):
    proxy_url = os.environ.get("PROXY_URL")

TOKENS = list()
# setup telegram bot client
req_proxy = telegram.request.HTTPXRequest(
    proxy_url=proxy_url,
    connect_timeout=30,
    read_timeout=30,
    write_timeout=30,
    pool_timeout=30,
)
bot = telegram.Bot(token=BOT_TOKEN, request=req_proxy)


# AD class model
class AD(BaseModel):
    title: str
    price: int
    description: str = ""
    district: str
    images: list[str] = []
    token: str
    features: list[tuple[str, str]] = []  # e.g. [("متراژ ویلا", "۱۰۰"), ...]
    posted_in: str = ""  # e.g. "علی‌آباد کتول، خ ابر-شیرین آباد-علی آباد"


def build_search_body():
    """Builds the JSON body for Divar's new postlist search API (first page only)."""
    return {
        "city_ids": SEARCH_CITY_IDS,
        "search_data": {
            "form_data": {
                "data": {
                    "category": {
                        "str": {"value": SEARCH_CATEGORY}
                    }
                }
            },
            "server_payload": {
                "@type": "type.googleapis.com/widgets.SearchData.ServerPayload",
                "additional_form_data": {
                    "data": {
                        "sort": {"str": {"value": "sort_date"}}
                    }
                },
            },
        },
        "disable_recommendation": False,
        "map_state": {"camera_info": {"bbox": {}}},
        "current_tab_slug": "default",
    }


def get_data():
    body = build_search_body()
    response = requests.post(URL, headers=REQUEST_HEADERS, json=body)
    print(
        "{} - Got response: {}".format(
            datetime.datetime.now(), response.status_code
        )
    )
    response.raise_for_status()
    return response.json()


def get_ads_list(data):
    # New API returns posts under "list_widgets" (not "widget_list")
    return data.get("list_widgets", [])


DEBUG_DUMP_SECTIONS = os.environ.get("DEBUG_DUMP_SECTIONS", "") == "1"
_debug_dumped_once = False


def extract_features(sections):
    """Walks the LIST_DATA section (and any nested amenities modal inside it)
    and returns a list of (label, value) pairs for every structured field
    Divar shows on the ad page (metrage, capacity, rent prices, amenities...).
    """
    features = []

    def process_widgets(widgets):
        pending_label = None
        for w in widgets:
            wtype = w.get("widget_type")
            data = w.get("data", {})

            if wtype == "GROUP_INFO_ROW":
                for item in data.get("items", []):
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
                chips = data.get("chip_list", {}).get("chips", [])
                chip_texts = [c.get("text", "") for c in chips if c.get("text")]
                if chip_texts:
                    features.append((pending_label or "ویژگی", "، ".join(chip_texts)))
                pending_label = None

            elif wtype == "SELECTOR_ROW":
                # amenities are often tucked inside a modal opened by this row
                modal = (
                    data.get("action", {})
                    .get("payload", {})
                    .get("modal_page", {})
                )
                nested = modal.get("widget_list")
                if nested:
                    process_widgets(nested)

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
                    return w.get("data", {}).get("title", "")
    return ""


def fetch_ad_data(token: str) -> AD:
    # send request
    response = requests.get(
        f"https://api.divar.ir/v8/posts-v2/web/{token}", headers=REQUEST_HEADERS
    )
    data = response.json()
    images = []
    # check post exists
    if "sections" not in data:
        print(
            "Warning: unexpected response for token {} (status {}): {}".format(
                token, response.status_code, str(data)[:300]
            )
        )
        return None

    if DEBUG_DUMP_SECTIONS:
        global _debug_dumped_once
        if not _debug_dumped_once:
            _debug_dumped_once = True
            print("===== FULL SECTIONS DUMP for token {} =====".format(token))
            print(json.dumps(data["sections"], ensure_ascii=False))
            print("===== END DUMP =====")

    title = ""
    description = ""

    try:
        # get data
        for section in data["sections"]:
            # find title section
            if section["section_name"] == "TITLE":
                title = section["widgets"][0]["data"]["title"]

            # find images section
            if section["section_name"] == "IMAGE":
                images = section["widgets"][0]["data"]["items"]
                images = [img["image"]["url"] for img in images]

            # find description section
            if section["section_name"] == "DESCRIPTION":
                description = section["widgets"][1]["data"]["text"]

        # get district (fall back gracefully if missing)
        district = (
            data.get("seo", {}).get("web_info", {}).get("district_persian", "")
        )
        price = data.get("webengage", {}).get("price", 0) or 0

        features = extract_features(data["sections"])
        posted_in = extract_posted_in(data["sections"])

        # create ad object
        ad = AD(
            token=token,
            title=title,
            district=district,
            description=description,
            images=images,
            price=price,
            features=features,
            posted_in=posted_in,
        )
    except (KeyError, IndexError, TypeError) as e:
        print("Warning: failed to parse ad {} ({}), skipping.".format(token, e))
        return None

    return ad


# --- Hashtag keyword mapping -------------------------------------------
# Each hashtag maps to a list of keywords/variants to search for in the
# ad's title, description, and district (any match triggers the tag).
CATEGORY_HASHTAGS = {
    "مسکونی": ["مسکونی"],
    "آپارتمان": ["آپارتمان", "اپارتمان"],
    "ویلایی": ["ویلایی", "ویلا"],
    "باغ": ["باغ"],
    "زمین": ["زمین"],
    "کشاورزی": ["کشاورزی"],
    "تجاری": ["تجاری"],
    "مغازه": ["مغازه"],
    "اداری": ["اداری"],
}

LOCATION_HASHTAGS = {
    "فاضل_آباد": ["فاضل آباد", "فاضل‌آباد", "فاضلاباد"],
    "علی_آباد_کتول": ["علی آباد کتول", "علی‌آباد کتول", "علی اباد کتول", "علی آباد"],
    "مزرعه": ["مزرعه"],
}

DEAL_TYPE_HASHTAGS = {
    "اجاره": ["اجاره"],
    "رهن": ["رهن"],
    "فروش": ["فروش"],
}

ALL_HASHTAG_GROUPS = [CATEGORY_HASHTAGS, LOCATION_HASHTAGS, DEAL_TYPE_HASHTAGS]


def generate_hashtags(ad: "AD") -> list[str]:
    """Scans the ad's title/description/district/posted_in for known
    keywords and returns a list of matching hashtags (without the #)."""
    haystack = " ".join(
        [ad.title or "", ad.description or "", ad.district or "", ad.posted_in or ""]
    )

    tags = []
    for group in ALL_HASHTAG_GROUPS:
        for tag, keywords in group.items():
            if any(keyword in haystack for keyword in keywords):
                tags.append(tag)
    return tags


async def send_telegram_message(ad: AD):
    text = f"🗄 <b>{html.escape(ad.title)}</b>" + "\n"
    location_line = ad.posted_in or ad.district
    if location_line:
        text += f"📌 محل آگهی : <i>{html.escape(location_line)}</i>" + "\n"
    _price = f"{ad.price:,} تومان" if ad.price else "توافقی"
    text += f"💰 قیمت : {_price}" + "\n"

    if ad.features:
        text += "\n📋 <b>مشخصات</b> :\n"
        for label, value in ad.features:
            text += f"🔸 {html.escape(label)}: {html.escape(value)}\n"

    text += f"\n📄 توضیحات :\n{html.escape(ad.description)}" + "\n"
    text += f"https://divar.ir/v/a/{ad.token}"

    hashtags = generate_hashtags(ad)
    if hashtags:
        text += "\n\n" + " ".join(f"#{tag}" for tag in hashtags)

    # send single photo
    if len(ad.images) == 1:
        await bot.send_photo(
            caption=text, photo=ad.images[0], chat_id=BOT_CHATID, parse_mode="HTML"
        )
    # send album
    elif len(ad.images) > 1:
        _media_list = [telegram.InputMediaPhoto(img) for img in ad.images[:10]]
        try:
            await bot.send_media_group(
                caption=text, media=_media_list, chat_id=BOT_CHATID, parse_mode="HTML"
            )
        except telegram.error.BadRequest as e:
            print("Error sending photos :", e)
            return
    else:
        # send just text
        await bot.send_message(text=text, chat_id=BOT_CHATID, parse_mode="HTML")


def load_tokens():
    token_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "tokens.json"
    )
    try:
        with open(token_path, "r") as content:
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


def save_tokns(tokens):
    token_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "tokens.json"
    )
    with open(token_path, "w") as outfile:
        json.dump(tokens, outfile)


def get_tokens_page():
    data = get_data()
    data = get_ads_list(data)
    print("Raw list_widgets length: {}".format(len(data)))
    data = data[::-1]
    # get tokens - only real post rows (skip banners, blocking views, etc.)
    data = filter(lambda x: x.get("widget_type") == "POST_ROW", data)
    tokens = list(map(lambda x: x["data"]["token"], data))
    return tokens


async def process_data(tokens):
    for token in tokens:
        # get the ad data
        ad = fetch_ad_data(token)
        if not ad:
            continue
        print("AD - {} - {}".format(token, vars(ad)))
        # send message to telegram (retry once on transient timeout)
        print("sending to telegram token: {}".format(ad.token))
        for attempt in range(2):
            try:
                await send_telegram_message(ad)
                break
            except telegram.error.TimedOut:
                if attempt == 0:
                    print(
                        "Timed out sending {}, retrying once...".format(ad.token)
                    )
                    time.sleep(3)
                else:
                    print(
                        "Timed out again sending {}, skipping this ad.".format(
                            ad.token
                        )
                    )
        time.sleep(1)


if __name__ == "__main__":
    print("Started at {}.".format(datetime.datetime.now()))
    tokens = load_tokens()
    print("Tokens length: {}".format(len(tokens)))

    # get new tokens list (single page - newest ads sorted by date)
    tokens_list = get_tokens_page()
    print("Fetched {} ads from Divar this run.".format(len(tokens_list)))
    # remove repeated tokens
    tokens_list = list(filter(lambda t: t not in tokens, tokens_list))
    print("{} of them are new (not seen before).".format(len(tokens_list)))
    tokens = list(set(tokens_list + tokens))
    asyncio.run(process_data(tokens_list))

    # save new tokens
    save_tokns(tokens)
    print("Finished at {}.".format(datetime.datetime.now()))
