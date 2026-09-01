"""Independent polling worker. Run with --once for a controlled manual cycle."""
from __future__ import annotations
import argparse
import asyncio
from datetime import timedelta
import json
import signal

from .config import Settings
from .contracts import utcnow
from .db import get_engine, seed_sources
from .ingestion import (
    LeaseLost, apply_retention, as_time, claim_source, expire_advisories, fail_source, persist_batch,
    release_interrupted_lease,
)
from .network import FetchError, SafeHTTPClient
from .providers import MissingCredentials, ProviderError, SOURCES, collect


def transaction(engine, function, *args, **kwargs):
    with engine.begin() as conn:
        return function(conn, *args, **kwargs)


async def run_source(engine, fetcher, settings, lease):
    try:
        config = {"radar_token": settings.radar_token, "meteoalarm_country": "poland"}
        repair = as_time(lease.cursor.get("last_repair_at"))
        if lease.source_id == "usgs" and (repair is None or utcnow() - repair > timedelta(hours=6)):
            config["usgs_window"] = "week"
        async with asyncio.timeout(max(30, settings.worker_lease_seconds - 60)):
            batch = await collect(lease.source_id, fetcher, config)
        if config.get("usgs_window") == "week":
            batch.metadata["repair_window"] = "week"
        result = await asyncio.to_thread(transaction, engine, persist_batch, lease, batch)
    except asyncio.CancelledError:
        try:
            await asyncio.to_thread(transaction, engine, release_interrupted_lease, lease)
        except Exception:
            pass  # Database outage: the bounded lease expires without any overwrite.
        raise
    except LeaseLost:
        result = {"source": lease.source_id, "status": "lease_lost"}
    except Exception as exc:
        if isinstance(exc, (FetchError, ProviderError)):
            message = str(exc)
        else:
            # Never log response bodies, SQL parameters, database URLs or token values.
            message = f"Błąd przetwarzania ({type(exc).__name__}); ostatnie poprawne dane zachowano."
        try:
            result = await asyncio.to_thread(
                transaction, engine, fail_source, lease, message,
                retry_after=getattr(exc, "retry_after_seconds", None),
                needs_credentials=isinstance(exc, MissingCredentials),
            )
        except LeaseLost:
            result = {"source": lease.source_id, "status": "lease_lost"}
        except Exception:
            result = {"source": lease.source_id, "status": "database_unavailable"}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


async def run(*, once=False, source_id=None):
    settings = Settings.from_env()
    engine = get_engine(settings.database_url)
    await asyncio.to_thread(transaction, engine, seed_sources, radar_enabled=bool(settings.radar_token))
    async with SafeHTTPClient() as fetcher:
        if once:
            semaphore = asyncio.Semaphore(3)
            async def one(id):
                async with semaphore:
                    lease = await asyncio.to_thread(transaction, engine, claim_source, id, force=True)
                    return await run_source(engine, fetcher, settings, lease) if lease else {"source": id, "status": "skipped"}
            results = await asyncio.gather(*(one(id) for id in ([source_id] if source_id else SOURCES)))
            await asyncio.to_thread(transaction, engine, expire_advisories)
            return not any(row["status"] in {"error", "lease_lost"} for row in results)
        jobs = set()
        last_maintenance = None
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(stop_signal, stop.set)
        try:
            while not stop.is_set():
                now = utcnow()
                if last_maintenance is None or now - last_maintenance >= timedelta(minutes=5):
                    await asyncio.to_thread(transaction, engine, expire_advisories)
                    if last_maintenance is None or now.hour != last_maintenance.hour:
                        await asyncio.to_thread(transaction, engine, apply_retention)
                    last_maintenance = now
                jobs = {job for job in jobs if not job.done()}
                while len(jobs) < 3 and not stop.is_set():
                    lease = await asyncio.to_thread(transaction, engine, claim_source)
                    if lease is None:
                        break
                    jobs.add(asyncio.create_task(run_source(engine, fetcher, settings, lease)))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except TimeoutError:
                    pass
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--source", choices=tuple(SOURCES))
    args = parser.parse_args()
    try:
        success = asyncio.run(run(once=args.once, source_id=args.source))
    except KeyboardInterrupt:
        return
    if args.once and not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
