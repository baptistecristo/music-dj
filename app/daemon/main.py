"""Entrypoint.

    python -m daemon.main            # profile picking (milestone 2)
    python -m daemon.main --verbose
"""

import argparse
import asyncio
import logging

from . import advisor, core, server, store


def setup_logging(verbose):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S")
    # websockets logs every frame at DEBUG, and the player sends a position
    # update every second, so --verbose otherwise buries our own lines under
    # protocol chatter.
    logging.getLogger("websockets").setLevel(logging.WARNING)


async def run(args):
    config = store.read_json(store.CONFIG, {})
    transport = server.BridgeTransport()
    dj = core.DJ(transport, config=config)

    if not args.no_claude:
        # Claude picks the batch; the profile stays underneath as the fallback
        # whenever the CLI is missing, slow, or unhelpful.
        dj.picks_for = lambda mood, lane: advisor.picks_for(
            mood, lane, seeds=dj.seeds, rng=dj.rng)
    transport.on_event = dj.on_event

    srv = server.Server(dj, transport, port=args.port)
    logging.getLogger("music-dj").info(
        "mood %s (lane %s); waiting for the extension", dj.mood, dj.lane)

    runner = asyncio.ensure_future(asyncio.gather(
        srv.run(), dj.watch_state(), start_when_ready(dj)))
    stopper = asyncio.ensure_future(dj.shutdown_event.wait())
    done, _ = await asyncio.wait([runner, stopper],
                                 return_when=asyncio.FIRST_COMPLETED)
    runner.cancel()
    stopper.cancel()
    # A crash (say, the port already in use) also lands here. Read the
    # exception rather than dropping it, or the daemon dies silently with
    # exit code 0 and nothing in the log to say why.
    failed = False
    for task in done:
        exc = None if task.cancelled() else task.exception()
        if exc is not None:
            logging.getLogger("music-dj").error("daemon failed: %s", exc,
                                                exc_info=exc)
            failed = True
    # The shutdown broadcast to the overlay rides on tasks created just
    # before the event was set; give them a beat to flush.
    await asyncio.sleep(0.2)
    logging.getLogger("music-dj").info("daemon stopped")
    if failed:
        raise SystemExit(1)


async def start_when_ready(dj, poll=3.0):
    """Begin playing as soon as the extension shows up, not before.

    ensure_playing() does the "is anything already starting?" check itself;
    testing dj.current here would start a second track over the first while
    the first was still being confirmed.
    """
    while True:
        if dj.tx.connected:
            await dj.ensure_playing()
        await asyncio.sleep(poll)


def main():
    parser = argparse.ArgumentParser(prog="music-dj daemon")
    parser.add_argument("--port", type=int, default=server.PORT)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-claude", action="store_true",
                        help="pick from the taste profile only")
    args = parser.parse_args()
    setup_logging(args.verbose)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
