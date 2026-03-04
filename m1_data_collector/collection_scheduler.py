"""
MKT-8: collection_scheduler
Orchestrates all M1 collection strategies in sequence.
Designed to run daily via cron or Dronor pipeline.
"""
import sys, logging
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))

from m1_data_collector.twitter_search_profiles import twitter_search_profiles
from m1_data_collector.twitter_get_user_tweets import twitter_get_user_tweets
from m1_data_collector.profile_enricher import profile_enricher
from m1_data_collector.twitter_get_followers_of import twitter_get_followers_of
from m1_data_collector.twitter_search_conversations import twitter_search_conversations
from infra.db import execute_query

log = logging.getLogger(__name__)


def collection_scheduler(
    run_strategy_a: bool = True,
    run_strategy_c: bool = True,
    run_strategy_d: bool = True,
    run_tweet_fetch: bool = True,
    run_enricher: bool = True,
    profiles_per_strategy: int = 200,
    enrich_batch_size: int = 30
) -> dict:
    """
    Full M1 collection pipeline. Run daily.
    Steps: A+C+D collection → tweet fetch → LLM enrichment.

    Returns summary of all steps.
    """
    started_at = datetime.utcnow()
    results = {"started_at": started_at.isoformat(), "steps": {}}

    # Step 1: Collect profiles via strategies
    if run_strategy_a:
        log.info("[Scheduler] Strategy A: keyword search")
        r = twitter_search_profiles(
            use_default_queries=True,
            max_profiles=profiles_per_strategy
        )
        results["steps"]["strategy_a"] = r
        log.info(f"[Scheduler] A done: saved={r.get('profiles_saved')}")

    if run_strategy_c:
        log.info("[Scheduler] Strategy C: follower harvest")
        r = twitter_get_followers_of(
            use_default_targets=True,
            max_followers=profiles_per_strategy
        )
        results["steps"]["strategy_c"] = r
        log.info(f"[Scheduler] C done: saved={r.get('saved')}")

    if run_strategy_d:
        log.info("[Scheduler] Strategy D: conversation mining")
        r = twitter_search_conversations(
            use_default_queries=True,
            max_profiles=profiles_per_strategy
        )
        results["steps"]["strategy_d"] = r
        log.info(f"[Scheduler] D done: saved={r.get('saved')}")

    # Step 2: Fetch tweets for new profiles
    if run_tweet_fetch:
        log.info("[Scheduler] Fetching tweets for new profiles")
        r = twitter_get_user_tweets(batch_size=100, max_tweets=20)
        results["steps"]["tweet_fetch"] = r
        log.info(f"[Scheduler] Tweets done: processed={r.get('processed')}, saved={r.get('tweets_saved')}")

    # Step 3: LLM enrichment
    if run_enricher:
        log.info("[Scheduler] LLM enrichment")
        r = profile_enricher(batch_size=enrich_batch_size)
        results["steps"]["enricher"] = r
        log.info(f"[Scheduler] Enriched: {r.get('enriched')}/{r.get('processed')}")

    # Summary stats
    total_profiles = execute_query("SELECT COUNT(*) as n FROM twitter_profiles", fetch="one")
    enriched_profiles = execute_query(
        "SELECT COUNT(*) as n FROM twitter_profiles WHERE professional_role IS NOT NULL", fetch="one"
    )

    results["summary"] = {
        "status": "success",
        "total_profiles_in_db": total_profiles["n"] if total_profiles else 0,
        "enriched_profiles": enriched_profiles["n"] if enriched_profiles else 0,
        "duration_seconds": (datetime.utcnow() - started_at).seconds
    }

    log.info(f"[Scheduler] Done. Total profiles: {results['summary']['total_profiles_in_db']}")
    return results
