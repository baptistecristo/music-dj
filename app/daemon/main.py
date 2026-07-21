"""Entrypoint.

    python -m daemon.main            # profile picking (milestone 2)
    python -m daemon.main --verbose
"""

import argparse
import asyncio
import logging

from . import core, server, store


def setup_logging(verbose):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S")
    # websockets logs every frame at DEBUG, and the player sends a position
    # update every second, so --verbose otherwise buries our own lines under
    # protocol chatter.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    # websockets logs every frame at DEBUG, and the player sends a position
    # update every second, so --verbose otherwise buries our own lines under
    # protocol chatter.
    logging.getLogger("websockets").setLevel(logging.WARNING)


async def run(args):
    config = store.read_json(store.CONFIG, {})
    transport = server.BridgeTransport()
    dj = core.DJ(transport, config=config)
    transport.on_event = dj.on_event

    srv = server.Server(dj, transport, port=args.port)
    logging.getLogger("music-dj").info(
        "mood %s (lane %s); waiting for the extension", dj.mood, dj.lane)

    await asyncio.gather(srv.run(), dj.watch_state(), start_when_ready(dj))


async def start_when_ready(dj, poll=2.0):
    """Begin playing as soon as the extension shows up, not before."""
    while True:
        if dj.tx.connected and dj.current is None:
            await dj.play_next()
        await asyncio.sleep(poll)


def main():
    parser = argparse.ArgumentParser(prog="music-dj daemon")
    parser.add_argument("--port", type=int, default=server.PORT)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
