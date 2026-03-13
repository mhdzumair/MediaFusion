import argparse
import asyncio
import json
import logging
import statistics
import time
from dataclasses import replace
from typing import Any

from db.config import settings
from db.schemas import MetadataData
from scrapers.public_indexer_registry import PUBLIC_INDEXER_DEFINITIONS
from scrapers.public_indexers import PublicIndexerScraper
from scrapers.source_health import get_source_health, record_source_outcome

DEFAULT_ANIME_SCENARIOS: tuple[tuple[str, int | None, int, int], ...] = (
    ("One Piece", 1999, 1, 1),
    ("Bleach", 2004, 1, 1),
    ("Naruto Shippuden", 2007, 1, 1),
    ("Jujutsu Kaisen", 2020, 1, 1),
    ("Attack on Titan", 2013, 1, 1),
    ("Demon Slayer", 2019, 1, 1),
    ("Solo Leveling", 2024, 1, 1),
    ("Frieren", 2023, 1, 1),
)


def _configure_logging() -> None:
    for logger_name in (
        "scrapling",
        "scrapling.fetchers",
        "scrapling.engines._browsers._stealth",
        "scrapling.engines.toolbelt.custom",
    ):
        logging.getLogger(logger_name).setLevel(logging.ERROR)


def _parse_source_ids(raw: str | None) -> list[str]:
    configured = (raw or "").strip()
    if not configured:
        configured = settings.public_indexers_anime_live_search_sites
    source_ids = [item.strip().lower() for item in configured.split(",") if item.strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for source_id in source_ids:
        if source_id in seen:
            continue
        seen.add(source_id)
        deduped.append(source_id)
    return deduped


def _build_metadata(indexer_key: str, title: str, year: int | None, scenario_idx: int) -> MetadataData:
    return MetadataData(
        id=10_000 + scenario_idx,
        external_id=f"mf:anime-stress:{indexer_key}:{scenario_idx}",
        type="series",
        title=title,
        year=year,
        genres=["anime"],
        catalogs=["anime_series"],
    )


def _build_query(title: str, episode: int) -> str:
    return f"{title} {episode:02d}"


async def _run_probe(
    scraper: PublicIndexerScraper,
    *,
    indexer_key: str,
    title: str,
    year: int | None,
    season: int,
    episode: int,
    timeout_seconds: int,
    scenario_idx: int,
) -> dict[str, Any]:
    definition = PUBLIC_INDEXER_DEFINITIONS[indexer_key]
    templated_definition = replace(
        definition,
        query_url_templates=definition.query_url_templates[:2],
        search_pages_per_query=1,
    )
    metadata = _build_metadata(indexer_key, title, year, scenario_idx)
    query = _build_query(title, episode)
    processed_info_hashes: set[str] = set()
    start = time.monotonic()

    async def _collect() -> dict[str, Any]:
        count = 0
        examples: list[str] = []
        async for stream in scraper._search_indexer(
            indexer=templated_definition,
            query=query,
            metadata=metadata,
            catalog_type="series",
            season=season,
            episode=episode,
            is_anime=True,
            processed_info_hashes=processed_info_hashes,
        ):
            count += 1
            if len(examples) < 2:
                examples.append(stream.name)
        return {"count": count, "examples": examples}

    try:
        result = await asyncio.wait_for(_collect(), timeout=timeout_seconds)
        status = "pass" if result["count"] > 0 else "empty"
        return {
            "indexer": indexer_key,
            "status": status,
            "count": result["count"],
            "examples": result["examples"],
            "elapsed_sec": round(time.monotonic() - start, 3),
        }
    except TimeoutError:
        await record_source_outcome(indexer_key, success=False, timed_out=True, challenge_solved=False)
        return {
            "indexer": indexer_key,
            "status": "timeout",
            "count": 0,
            "examples": [],
            "elapsed_sec": round(time.monotonic() - start, 3),
        }
    except Exception as exc:  # noqa: BLE001 - stress harness should continue on errors
        await record_source_outcome(indexer_key, success=False, timed_out=False, challenge_solved=False)
        return {
            "indexer": indexer_key,
            "status": "error",
            "count": 0,
            "examples": [],
            "error": str(exc),
            "elapsed_sec": round(time.monotonic() - start, 3),
        }


def _compute_stats(results: list[dict[str, Any]], source_ids: list[str]) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = {source_id: [] for source_id in source_ids}
    for result in results:
        by_source.setdefault(result["indexer"], []).append(result)

    ranked: list[dict[str, Any]] = []
    for source_id, rows in by_source.items():
        total = len(rows)
        passes = sum(1 for row in rows if row["status"] == "pass")
        empties = sum(1 for row in rows if row["status"] == "empty")
        timeouts = sum(1 for row in rows if row["status"] == "timeout")
        errors = sum(1 for row in rows if row["status"] == "error")
        stream_hits = sum(int(row.get("count", 0) or 0) for row in rows)
        latencies = [float(row.get("elapsed_sec", 0.0) or 0.0) for row in rows]
        p95_latency = (
            round(statistics.quantiles(latencies, n=20)[18], 3) if len(latencies) >= 20 else max(latencies, default=0.0)
        )
        avg_latency = round(sum(latencies) / len(latencies), 3) if latencies else 0.0
        pass_rate = round((passes / total), 4) if total else 0.0
        timeout_rate = round((timeouts / total), 4) if total else 0.0

        ranked.append(
            {
                "source": source_id,
                "attempts": total,
                "pass": passes,
                "empty": empties,
                "timeout": timeouts,
                "error": errors,
                "stream_hits": stream_hits,
                "pass_rate": pass_rate,
                "timeout_rate": timeout_rate,
                "avg_latency_sec": avg_latency,
                "p95_latency_sec": p95_latency,
            }
        )

    return sorted(
        ranked,
        key=lambda row: (
            -row["pass_rate"],
            row["timeout_rate"],
            -row["stream_hits"],
            row["avg_latency_sec"],
            row["source"],
        ),
    )


async def _collect_source_health(source_ids: list[str]) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for source_id in source_ids:
        health = await get_source_health(source_id)
        snapshot[source_id] = {
            "samples": health.total,
            "success": health.success,
            "timeouts": health.timeout,
            "challenge_solved": health.challenge_solved,
            "success_rate": round(health.success_rate, 4),
            "timeout_rate": round(health.timeout_rate, 4),
            "challenge_solve_rate": round(health.challenge_solve_rate, 4),
        }
    return snapshot


async def main() -> None:
    parser = argparse.ArgumentParser(description="Stress-test anime live scraping with public indexers.")
    parser.add_argument("--rounds", type=int, default=4, help="How many rounds to run for each scenario/source pair.")
    parser.add_argument("--concurrency", type=int, default=12, help="Maximum concurrent scrape probes.")
    parser.add_argument("--timeout", type=int, default=30, help="Per-probe timeout in seconds.")
    parser.add_argument(
        "--sources",
        type=str,
        default="",
        help="Comma-separated source IDs. Defaults to public_indexers_anime_live_search_sites.",
    )
    args = parser.parse_args()

    _configure_logging()
    scraper = PublicIndexerScraper()

    source_ids = [source_id for source_id in _parse_source_ids(args.sources) if source_id in PUBLIC_INDEXER_DEFINITIONS]
    if not source_ids:
        raise RuntimeError("No valid source IDs found for anime stress harness.")

    scenarios = list(DEFAULT_ANIME_SCENARIOS)
    semaphore = asyncio.Semaphore(max(1, int(args.concurrency)))
    results: list[dict[str, Any]] = []

    async def _bounded_probe(
        *,
        indexer_key: str,
        title: str,
        year: int | None,
        season: int,
        episode: int,
        scenario_idx: int,
    ) -> None:
        async with semaphore:
            result = await _run_probe(
                scraper,
                indexer_key=indexer_key,
                title=title,
                year=year,
                season=season,
                episode=episode,
                timeout_seconds=max(10, int(args.timeout)),
                scenario_idx=scenario_idx,
            )
            results.append(result)
            print(
                f"[{result['status']}] {indexer_key} :: {title} S{season:02d}E{episode:02d} -> {result['count']} ({result['elapsed_sec']}s)",
                flush=True,
            )

    start = time.monotonic()
    tasks: list[asyncio.Task[None]] = []
    scenario_idx = 0
    for round_idx in range(max(1, int(args.rounds))):
        for title, year, season, episode in scenarios:
            scenario_idx += 1
            # Spread episode numbers by round to increase query diversity.
            current_episode = max(1, episode + round_idx)
            for source_id in source_ids:
                task = asyncio.create_task(
                    _bounded_probe(
                        indexer_key=source_id,
                        title=title,
                        year=year,
                        season=season,
                        episode=current_episode,
                        scenario_idx=scenario_idx,
                    )
                )
                tasks.append(task)

    await asyncio.gather(*tasks)
    total_elapsed = round(time.monotonic() - start, 3)

    ranked = _compute_stats(results, source_ids)
    health_snapshot = await _collect_source_health(source_ids)

    summary = {
        "sources": source_ids,
        "rounds": max(1, int(args.rounds)),
        "scenario_count": len(scenarios),
        "probe_count": len(results),
        "concurrency": max(1, int(args.concurrency)),
        "timeout_seconds": max(10, int(args.timeout)),
        "elapsed_sec": total_elapsed,
        "ranking": ranked,
        "source_health": health_snapshot,
        "gate_thresholds": {
            "enabled": settings.public_indexers_source_health_gates_enabled,
            "min_samples": settings.public_indexers_source_health_min_samples,
            "min_success_rate": settings.public_indexers_source_min_success_rate,
            "max_timeout_rate": settings.public_indexers_source_max_timeout_rate,
        },
    }

    print("\nANIME_STRESS_SUMMARY_JSON_START")
    print(json.dumps(summary, indent=2))
    print("ANIME_STRESS_SUMMARY_JSON_END")


if __name__ == "__main__":
    asyncio.run(main())
