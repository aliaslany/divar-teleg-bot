"""Entry point: checks Divar for new ads matching the configured search,
and sends a Telegram message for each one not seen before."""
import asyncio
import datetime
import time

import telegram

from divar_client import fetch_ad_data, get_tokens_page
from storage import load_phones, load_tokens, save_phones, save_tokens
from telegram_client import send_telegram_message


async def process_data(tokens, phones, on_delivery=None):
    """Return delivered tokens and checkpoint each acknowledged Telegram send.

    Fetch and delivery failures stay eligible for a future run. The optional
    callback runs after delivery and phone collection, so a later failure does
    not discard progress already made in this cycle.
    """
    delivered_tokens = []
    for token in tokens:
        ad = fetch_ad_data(token)
        if not ad:
            continue
        print("AD - {} - {}".format(token, vars(ad)))
        print("sending to telegram token: {}".format(ad.token))

        # send message to telegram (retry once on transient timeout)
        delivered = False
        for attempt in range(2):
            try:
                await send_telegram_message(ad)
                delivered = True
                break
            except telegram.error.TimedOut:
                if attempt == 0:
                    print("Timed out sending {}, retrying once...".format(ad.token))
                    time.sleep(3)
                else:
                    print(
                        "Timed out again sending {}, skipping this ad.".format(
                            ad.token
                        )
                    )
            except telegram.error.TelegramError as error:
                print(
                    "Telegram rejected {}, leaving it for a future run ({}).".format(
                        ad.token, type(error).__name__
                    )
                )
                break

        if delivered:
            if ad.phone:
                phones[ad.token] = {"phone": ad.phone, "title": ad.title}
            delivered_tokens.append(token)
            if on_delivery is not None:
                on_delivery(token)
        time.sleep(1)
    return delivered_tokens


def main():
    print("Started at {}.".format(datetime.datetime.now()))
    seen_tokens = list(dict.fromkeys(load_tokens()))
    seen_set = set(seen_tokens)
    print("Tokens length: {}".format(len(seen_tokens)))

    # single page - newest ads sorted by date
    new_tokens = get_tokens_page()
    print("Fetched {} ads from Divar this run.".format(len(new_tokens)))
    new_tokens = [token for token in dict.fromkeys(new_tokens) if token not in seen_set]
    print("{} of them are new (not seen before).".format(len(new_tokens)))

    phones = load_phones()

    def checkpoint(token):
        seen_tokens.append(token)
        seen_set.add(token)
        # Persist progress before another request can fail. Preserve insertion
        # order rather than rewriting the file in randomized set order.
        save_tokens(seen_tokens)
        save_phones(phones)

    delivered_tokens = asyncio.run(process_data(new_tokens, phones, checkpoint))
    print("{} ads delivered successfully.".format(len(delivered_tokens)))
    print("Finished at {}.".format(datetime.datetime.now()))


if __name__ == "__main__":
    main()
