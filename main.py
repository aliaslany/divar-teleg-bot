"""Fetch new Divar ads and deliver each to configured messengers."""
import asyncio
import datetime
import time

from divar_client import fetch_ad_data, get_tokens_page
from messenger_client import enabled_messengers, send_ad
from storage import load_state, save_state


async def process_data(tokens, state, messengers):
    for token in tokens:
        ad = fetch_ad_data(token)
        if not ad:
            continue
        print("AD - {} - {}".format(token, vars(ad)))

        delivered = state["delivered"]
        destinations = [
            messenger
            for messenger in messengers
            if token not in delivered.get(messenger, [])
        ]
        if not destinations:
            continue

        print("Sending {} to: {}".format(ad.token, ", ".join(destinations)))
        outcomes = await send_ad(ad, destinations)
        if token not in state["known_tokens"]:
            state["known_tokens"].append(token)
        for messenger, succeeded in outcomes.items():
            if succeeded:
                delivered.setdefault(messenger, []).append(token)
        time.sleep(1)


def main():
    print("Started at {}.".format(datetime.datetime.now()))
    messengers = enabled_messengers()
    if not messengers:
        raise RuntimeError("Configure at least one messenger token and chat ID.")

    state = load_state()
    known_tokens = set(state["known_tokens"])
    print("Known tokens length: {}".format(len(known_tokens)))

    # single page - newest ads sorted by date
    new_tokens = get_tokens_page()
    print("Fetched {} ads from Divar this run.".format(len(new_tokens)))
    pending_tokens = [
        token
        for token in new_tokens
        if token not in known_tokens
        or any(token not in state["delivered"].get(messenger, []) for messenger in messengers)
    ]
    print("{} ads need delivery this run.".format(len(pending_tokens)))

    asyncio.run(process_data(pending_tokens, state, messengers))

    save_state(state)
    print("Finished at {}.".format(datetime.datetime.now()))


if __name__ == "__main__":
    main()
