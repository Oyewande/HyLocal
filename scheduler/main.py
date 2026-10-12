from __future__ import annotations

from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from scheduler.jobs import cleanup_past_events, scrape_and_process, send_friday_digest, send_reminders  # noqa: E402

import asyncio  # noqa: E402


def run_scrape_job() -> None:
    """Wrapper so APScheduler (sync) can call the async scrape job."""
    asyncio.run(scrape_and_process())


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="Africa/Lagos")

    # Scrape job — Mon, Wed, Fri at 10am Lagos time
    scheduler.add_job(
        run_scrape_job,
        trigger=CronTrigger(day_of_week="mon,wed,fri", hour=10, minute=0),
        id="scrape_and_process",
        name="Scrape & process events",
        max_instances=1,
        replace_existing=True,
    )

    # Friday digest — every Friday at 4pm Lagos time
    scheduler.add_job(
        send_friday_digest,
        trigger=CronTrigger(day_of_week="fri", hour=16, minute=0),
        id="friday_digest",
        name="Send Friday digest",
        max_instances=1,
        replace_existing=True,
    )

    # Hourly reminder check
    scheduler.add_job(
        send_reminders,
        trigger=CronTrigger(minute=0),   # top of every hour
        id="send_reminders",
        name="Send event reminders",
        max_instances=1,
        replace_existing=True,
    )

    # Daily cleanup — deactivate past events every morning at 8am
    scheduler.add_job(
        cleanup_past_events,
        trigger=CronTrigger(hour=8, minute=0),
        id="cleanup_past_events",
        name="Deactivate past events",
        max_instances=1,
        replace_existing=True,
    )

    return scheduler


def run() -> None:
    scheduler = build_scheduler()
    logger.info("Scheduler starting — jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  • {job.name} | next run: {job.next_run_time}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    run()
