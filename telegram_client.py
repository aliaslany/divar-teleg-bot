"""Telegram bot setup and message formatting/sending."""
import html

import telegram

import config
from hashtags import generate_hashtags

bot = None
if config.BOT_TOKEN:
    req_proxy = telegram.request.HTTPXRequest(
        proxy_url=config.PROXY_URL,
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
    )
    bot = telegram.Bot(token=config.BOT_TOKEN, request=req_proxy)


def build_message_text(ad) -> str:
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

    text += f"\n📄 توضیحات :\n{html.escape(ad.description)}"

    hashtags = generate_hashtags(ad)
    if hashtags:
        text += "\n\n" + " ".join(f"#{tag}" for tag in hashtags)

    text += config.FOOTER_TEXT
    return text


def build_plain_message_text(ad) -> str:
    """Build a portable text-only version for non-Telegram APIs."""
    text = "🗄 {}\n".format(ad.title)

    location_line = ad.posted_in or ad.district
    if location_line:
        text += "📌 محل آگهی : {}\n".format(location_line)

    price = "{:,} تومان".format(ad.price) if ad.price else "توافقی"
    text += "💰 قیمت : {}\n".format(price)

    if ad.features:
        text += "\n📋 مشخصات :\n"
        for label, value in ad.features:
            text += "🔸 {}: {}\n".format(label, value)

    text += "\n📄 توضیحات :\n{}".format(ad.description)

    hashtags = generate_hashtags(ad)
    if hashtags:
        text += "\n\n" + " ".join("#{}".format(tag) for tag in hashtags)

    return text + config.FOOTER_TEXT


async def send_telegram_message(ad):
    if bot is None or not config.BOT_CHATID:
        raise RuntimeError("Telegram is not configured.")

    text = build_message_text(ad)

    if len(ad.images) == 1:
        await bot.send_photo(
            caption=text,
            photo=ad.images[0],
            chat_id=config.BOT_CHATID,
            parse_mode="HTML",
        )
    elif len(ad.images) > 1:
        media_list = [telegram.InputMediaPhoto(img) for img in ad.images[:10]]
        try:
            await bot.send_media_group(
                caption=text,
                media=media_list,
                chat_id=config.BOT_CHATID,
                parse_mode="HTML",
            )
        except telegram.error.BadRequest as e:
            print("Error sending photos :", e)
            return
    else:
        await bot.send_message(
            text=text, chat_id=config.BOT_CHATID, parse_mode="HTML"
        )
