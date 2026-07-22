"""Send new Divar ads to every configured messenger platform."""
import asyncio

import requests

import config
from telegram_client import build_plain_message_text, send_telegram_message


def enabled_messengers() -> list[str]:
    """Return configured messengers, in delivery order."""
    messengers = []
    if config.BOT_TOKEN and config.BOT_CHATID:
        messengers.append("telegram")
    if config.BALE_BOT_TOKEN and config.BALE_CHATID:
        messengers.append("bale")
    if config.RUBIKA_BOT_TOKEN and config.RUBIKA_CHATID:
        messengers.append("rubika")
    if config.EITAA_TOKEN and config.EITAA_CHATID:
        messengers.append("eitaa")
    return messengers


def _send_http_message(name: str, url: str, chat_id: str, text: str) -> bool:
    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=config.MESSENGER_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        print("Failed to send to {}: {}".format(name, error))
        return False

    print("Sent ad to {}.".format(name))
    return True


async def send_ad(ad, destinations: list[str]) -> dict[str, bool]:
    """Deliver an ad to selected destinations and report each outcome."""
    outcomes = {}
    text = build_plain_message_text(ad)

    for destination in destinations:
        try:
            if destination == "telegram":
                await send_telegram_message(ad)
                outcomes[destination] = True
            elif destination == "bale":
                outcomes[destination] = await asyncio.to_thread(
                    _send_http_message,
                    "Bale",
                    "{}/bot{}/sendMessage".format(
                        config.BALE_API_BASE_URL.rstrip("/"), config.BALE_BOT_TOKEN
                    ),
                    config.BALE_CHATID,
                    text,
                )
            elif destination == "rubika":
                outcomes[destination] = await asyncio.to_thread(
                    _send_http_message,
                    "Rubika",
                    "{}/{}/sendMessage".format(
                        config.RUBIKA_API_BASE_URL.rstrip("/"), config.RUBIKA_BOT_TOKEN
                    ),
                    config.RUBIKA_CHATID,
                    text,
                )
            elif destination == "eitaa":
                outcomes[destination] = await asyncio.to_thread(
                    _send_http_message,
                    "Eitaa",
                    "{}/{}/sendMessage".format(
                        config.EITAA_API_BASE_URL.rstrip("/"), config.EITAA_TOKEN
                    ),
                    config.EITAA_CHATID,
                    text,
                )
        except Exception as error:
            print("Failed to send to {}: {}".format(destination, error))
            outcomes[destination] = False

    return outcomes
