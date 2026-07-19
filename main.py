"""Entry point: checks Divar for new ads matching the configured search,
and sends a Telegram message for each one not seen before."""
import asyncio
import datetime
import time

import telegram

from divar_client import fetch_ad_data, get_tokens_page
from storage import load_tokens, save_tokens
from telegram_client import send_telegram_message


async def process_data(tokens):
    for token in tokens:
        ad = fetch_ad_data(token)
        if not ad:
            continue
        print("AD - {} - {}".format(token, vars(ad)))
        print("sending to telegram token: {}".format(ad.token))

        # send message to telegram (retry once on transient timeout)
        for attempt in range(2):
            try:
                await send_telegram_message(ad)
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
        time.sleep(1)


def main():
    print("Started at {}.".format(datetime.datetime.now()))
    seen_tokens = load_tokens()
    print("Tokens length: {}".format(len(seen_tokens)))

    # single page - newest ads sorted by date
    new_tokens = get_tokens_page()
    print("Fetched {} ads from Divar this run.".format(len(new_tokens)))
    new_tokens = list(filter(lambda t: t not in seen_tokens, new_tokens))
    print("{} of them are new (not seen before).".format(len(new_tokens)))

    all_tokens = list(set(new_tokens + seen_tokens))
    asyncio.run(process_data(new_tokens))

    save_tokens(all_tokens)
    print("Finished at {}.".format(datetime.datetime.now()))


if __name__ == "__main__":
    main()
