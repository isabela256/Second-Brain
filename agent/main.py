import logging
import socket
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import ollama_client
import tracker
from jobs import daily_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

# Safety net: bound any blocking socket call that doesn't set its own timeout,
# so a bad connection (e.g. a flaky network dropping mid-request) can't hang
# the whole run indefinitely.
socket.setdefaulttimeout(30)


def serve() -> None:
    tracker.init_db()
    ollama_client.pull_model()

    scheduler = BlockingScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(
        daily_job.run,
        # Every other day — the free JSearch tier is ~200 calls/month and one run
        # uses ~13, so daily would run dry mid-month.
        CronTrigger(
            day="1-31/2",
            hour=config.DIGEST_HOUR,
            minute=config.DIGEST_MINUTE,
            timezone=config.TIMEZONE,
        ),
        id="internship_digest",
        misfire_grace_time=3600,
    )
    log.info(
        "Scheduler started. Digest every other day at %02d:%02d %s.",
        config.DIGEST_HOUR,
        config.DIGEST_MINUTE,
        config.TIMEZONE,
    )
    scheduler.start()


def run_once() -> None:
    tracker.init_db()
    ollama_client.pull_model()
    daily_job.run()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run-once":
        run_once()
    else:
        serve()
