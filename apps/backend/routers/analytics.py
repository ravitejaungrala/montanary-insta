from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from typing import Optional
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload
import requests
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from requests_oauthlib import OAuth1Session
from core.database import get_db, SessionLocal
from core.config import TWITTER_API_KEY, TWITTER_API_SECRET
from models import User, SocialAccount, PublishedPost, PublishedPostPlatform, UserAnalyticsSnapshot
from routers.auth import get_current_user
from services.plan_service import check_feature
from services.ai_service import analyze_comments_sentiment, client as ai_client
from services.team_service import get_team_scope_user_ids, is_team_member
from services.social_service import (
    fetch_linkedin_org_comments, reply_linkedin_org_comment,
    fetch_facebook_comments, reply_facebook_comment,
    fetch_instagram_comments, reply_instagram_comment,
    fetch_twitter_comments, reply_twitter_comment,
)

logger = logging.getLogger("pipelyt.analytics")

router = APIRouter()

def get_linkedin_stats(account, post_map):
    """Fetch LinkedIn Page and Post stats migrating to 2026-03 versioned API.

    Now runs against the Community Management API app, which grants the
    r_member_profileAnalytics + r_member_postAnalytics scopes needed for
    personal-account analytics. Every LinkedIn API boundary is logged with
    a [LI_STATS] prefix so failures are easy to grep.
    """
    token = account.token
    acc_id = account.account_id

    logger.info(
        f"[LI_STATS] BEGIN account_id={acc_id!r} token_prefix={(token or '')[:10]}... "
        f"post_map_size={len(post_map or {})}"
    )

    # 1. Page Stats (Followers)
    # The 2026-03 docs states that networkSizes is the canonical way to get total counts
    target_urn = acc_id
    if not target_urn.startswith("urn:li:"):
        target_urn = f"urn:li:organization:{acc_id}"

    stats = {"followers": 0, "engagement": 0, "reach": 0}
    detailed_posts = []

    # Personal (member) accounts: with r_member_profileAnalytics granted by
    # the Community Management API app, we can now try /rest/networkSizes with
    # the FOLLOWER edge. Falls back to /v2/connections (r_1st_connections_size).
    is_org = "organization" in target_urn
    logger.info(f"[LI_STATS] resolved target_urn={target_urn!r} is_org={is_org}")

    # Version fallback chain: 202405 is the last LinkedIn version that has
    # both networkSizes and connections stable — try newest first, then
    # walk back. 202512 currently returns 426 (upgrade required) so it's
    # dropped from the list.
    versions = ["202603", "202405"]
    active_v = "202603"

    from urllib.parse import quote

    for v in versions:
        h = {
            "Authorization": f"Bearer {token}",
            "Linkedin-Version": v,
            "X-Restli-Protocol-Version": "2.0.0",
            "X-Restli-Protocol-Draft-Status": "PROMOTED",
        }
        try:
            if is_org:
                encoded_urn = quote(target_urn)
                url = (
                    f"https://api.linkedin.com/rest/networkSizes/{encoded_urn}"
                    f"?edgeType=COMPANY_FOLLOWED_BY_MEMBER"
                )
                res = requests.get(url, headers=h)
                logger.info(
                    f"[LI_STATS] org followers /rest/networkSizes v={v} "
                    f"status={res.status_code} body={res.text[:200]}"
                )
                if res.status_code == 200:
                    active_v = v
                    data = res.json()
                    stats["followers"] = int(data.get("firstDegreeSize", 0) or 0)
                    logger.info(
                        f"[LI_STATS] ✅ org followers = {stats['followers']}"
                    )
                    break
                else:
                    logger.warning(
                        f"[LI_STATS] ❌ org followers v={v} FAILED "
                        f"({res.status_code}) body={res.text[:200]}"
                    )
            else:
                # ─────────────────────────────────────────────────────────
                # Personal account followers — 4-step fallback chain
                # (see https://learn.microsoft.com/en-us/linkedin/marketing/
                #      community-management/members/follower-statistics):
                #   0) CANONICAL: /rest/memberFollowersCount?q=me
                #      Under r_member_profileAnalytics — this is THE endpoint
                #      LinkedIn docs specify for lifetime personal followers.
                #      Response: {"elements": [{"memberFollowersCount": N}]}
                #   A) /rest/networkSizes/{urn}?edgeType=MEMBER_FOLLOWED_BY_MEMBER
                #   B) /rest/memberFollowerStatistics/{urn}
                #   C) FALLBACK: /v2/connections?q=viewer (r_1st_connections_size)
                # ─────────────────────────────────────────────────────────
                got_followers = False
                encoded_urn = quote(target_urn)

                # (0) — memberFollowersCount (THE canonical endpoint)
                url_0 = "https://api.linkedin.com/rest/memberFollowersCount?q=me"
                try:
                    res_0 = requests.get(url_0, headers=h)
                    logger.info(
                        f"[LI_STATS] personal followers (0) memberFollowersCount q=me "
                        f"v={v} status={res_0.status_code} body={res_0.text[:300]}"
                    )
                    if res_0.status_code == 200:
                        data_0 = res_0.json() or {}
                        elements = data_0.get("elements", []) or []
                        if elements:
                            stats["followers"] = int(
                                elements[0].get("memberFollowersCount", 0) or 0
                            )
                        active_v = v
                        got_followers = True
                        logger.info(
                            f"[LI_STATS] ✅ personal followers via memberFollowersCount(q=me) "
                            f"= {stats['followers']}"
                        )
                        break
                except Exception as e_0:
                    logger.warning(f"[LI_STATS] personal followers (0) exception: {e_0}")

                # (A) — networkSizes with MEMBER_FOLLOWED_BY_MEMBER edge
                url_a = (
                    f"https://api.linkedin.com/rest/networkSizes/{encoded_urn}"
                    f"?edgeType=MEMBER_FOLLOWED_BY_MEMBER"
                )
                try:
                    res_a = requests.get(url_a, headers=h)
                    logger.info(
                        f"[LI_STATS] personal followers (A) networkSizes MEMBER_FOLLOWED "
                        f"v={v} status={res_a.status_code} body={res_a.text[:200]}"
                    )
                    if res_a.status_code == 200:
                        data_a = res_a.json() or {}
                        stats["followers"] = int(data_a.get("firstDegreeSize", 0) or 0)
                        active_v = v
                        got_followers = True
                        logger.info(
                            f"[LI_STATS] ✅ personal followers via networkSizes(MEMBER_FOLLOWED) "
                            f"= {stats['followers']}"
                        )
                        break
                except Exception as e_a:
                    logger.warning(f"[LI_STATS] personal followers (A) exception: {e_a}")

                # (B) — memberFollowerStatistics
                url_b = (
                    f"https://api.linkedin.com/rest/memberFollowerStatistics/{encoded_urn}"
                )
                try:
                    res_b = requests.get(url_b, headers=h)
                    logger.info(
                        f"[LI_STATS] personal followers (B) memberFollowerStatistics "
                        f"v={v} status={res_b.status_code} body={res_b.text[:200]}"
                    )
                    if res_b.status_code == 200:
                        data_b = res_b.json() or {}
                        # Try common field names
                        stats["followers"] = int(
                            data_b.get("followerCount")
                            or data_b.get("totalFollowers")
                            or data_b.get("firstDegreeSize")
                            or 0
                        )
                        active_v = v
                        got_followers = True
                        logger.info(
                            f"[LI_STATS] ✅ personal followers via memberFollowerStatistics "
                            f"= {stats['followers']}"
                        )
                        break
                except Exception as e_b:
                    logger.warning(f"[LI_STATS] personal followers (B) exception: {e_b}")

                # (C) — /v2/connections?q=viewer (fallback, 1st-degree count)
                v2_url = (
                    "https://api.linkedin.com/v2/connections"
                    "?q=viewer&start=0&count=1"
                )
                v2_h = {
                    "Authorization": f"Bearer {token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                }
                res = requests.get(v2_url, headers=v2_h)
                logger.info(
                    f"[LI_STATS] personal followers (C) /v2/connections "
                    f"status={res.status_code} body={res.text[:200]}"
                )
                if res.status_code == 200:
                    active_v = v
                    data = res.json() or {}
                    paging = data.get("paging") or {}
                    stats["followers"] = int(paging.get("total", 0) or 0)
                    got_followers = True
                    logger.info(
                        f"[LI_STATS] ✅ personal followers via /v2/connections "
                        f"= {stats['followers']} (this is 1st-degree connection count)"
                    )
                    break
                else:
                    logger.warning(
                        f"[LI_STATS] ❌ personal followers ALL 3 paths failed "
                        f"(final /v2/connections {res.status_code} body={res.text[:200]})"
                    )
                    # /v2/ is version-independent — no point looping other versions.
                    break
        except Exception as e:
            logger.error(f"LinkedIn Profile Stats Exception ({v}): {str(e)}")

    # 2. Post Stats using the identified active version
    post_h = {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": active_v,
        "X-Restli-Protocol-Version": "2.0.0",
        "X-Restli-Protocol-Draft-Status": "PROMOTED"
    }
    # Fallback header set without the X-Restli-Protocol-Draft-Status —
    # some LinkedIn versioned endpoints reject the PROMOTED draft hint
    # with a 400 even though the rest of the call is valid.
    post_h_no_proto = {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": active_v,
        "X-Restli-Protocol-Version": "2.0.0",
    }

    def normalize_linkedin_post_urn(native_post_id):
        if not native_post_id:
            return None
        pid = str(native_post_id).strip()
        if not pid:
            return None
        if pid.startswith("urn:li:"):
            return pid
        return f"urn:li:share:{pid}"

    # ─────────────────────────────────────────────────────────────────
    # CIRCUIT BREAKER — kill the sync run at the first 429.
    # LinkedIn returns 429 when the day/second-throttle is hit; further
    # calls will ALL 429 too. Old code kept hammering (6 more calls per
    # post × N remaining posts), which turned one throttle event into
    # thousands of 429s in the log and made the throttle window LONGER.
    # This state is checked at every LinkedIn call site — first 429 flips
    # the flag, and every subsequent fetch short-circuits.
    # ─────────────────────────────────────────────────────────────────
    _circuit = {"tripped": False, "reason": ""}

    def _trip(reason: str) -> None:
        if not _circuit["tripped"]:
            _circuit["tripped"] = True
            _circuit["reason"] = reason
            logger.warning(
                f"[LI_STATS] 🔌 CIRCUIT BREAKER TRIPPED — {reason} — "
                f"aborting remaining post-metric fetches for this sync run. "
                f"LinkedIn daily throttle is hit; further calls would all "
                f"429 too. Will retry on next sync tick after throttle resets."
            )

    def fetch_v2_social_counts(post_urn):
        """Personal-post fallback: /rest/socialActions is partner-only
        (returns 403 ACCESS_DENIED / partnerApiSocialActions), but the older
        /v2/socialActions/{urn}/likes and .../comments endpoints are still
        accessible under w_member_social for the author's own posts.
        We count via paging.total on a count=1 page.
        """
        from urllib.parse import quote

        if _circuit["tripped"]:
            return None

        encoded = quote(post_urn, safe="")
        v2_h = {
            "Authorization": f"Bearer {token}",
            "X-Restli-Protocol-Version": "2.0.0",
        }

        def _count(kind):
            if _circuit["tripped"]:
                return None
            url = f"https://api.linkedin.com/v2/socialActions/{encoded}/{kind}?count=1"
            try:
                r = requests.get(url, headers=v2_h)
                if r.status_code == 429:
                    _trip(f"v2 socialActions/{kind} 429 on {post_urn}")
                    return None
                if r.status_code == 404:
                    # Post doesn't exist / was deleted — don't retry this call
                    # elsewhere in the chain.
                    return None
                if r.status_code != 200:
                    logger.warning(
                        f"LinkedIn v2 socialActions/{kind} failed ({r.status_code}) "
                        f"for {post_urn} body={r.text[:200]}"
                    )
                    return None
                paging = (r.json() or {}).get("paging") or {}
                return int(paging.get("total", 0) or 0)
            except Exception as e:
                logger.warning(f"LinkedIn v2 socialActions/{kind} exception for {post_urn}: {e}")
                return None

        likes = _count("likes")
        comments = _count("comments")
        if likes is None and comments is None:
            return None
        result = {
            "likes": likes or 0,
            "comments": comments or 0,
            "shares": 0,  # /v2/ has no share-count endpoint for personal posts
            "reach": 0,
        }
        logger.info(
            f"LinkedIn v2 socialActions OK for {post_urn} "
            f"likes={result['likes']} comments={result['comments']}"
        )
        return result

    def fetch_member_creator_post_analytics(post_urn):
        """PERSONAL post metrics via Community Management API.

        Endpoint: GET /rest/memberCreatorPostAnalytics?q=entity&entity=(share:<urn>)&queryType=X&aggregation=TOTAL
        Requires: r_member_postAnalytics scope (Community Management app).

        This lives in a DIFFERENT resource-throttle bucket than the legacy
        /v2/socialActions and /rest/socialActions paths — so it can still
        return 200 when those are day-capped.

        Fires one call per metric (REACTION, COMMENT, RESHARE, IMPRESSION)
        — 4 calls per post. Circuit breaker still applies.
        """
        from urllib.parse import quote

        if _circuit["tripped"]:
            return None

        # Build the entity parameter per LinkedIn docs. Only the URN's
        # internal colons are URL-encoded; the wrapping (share:...) parens
        # stay literal.
        entity_kind = "share" if ":share:" in post_urn else "ugc"
        encoded_urn_inner = quote(post_urn, safe="")
        entity_param = f"(share:{encoded_urn_inner})" if entity_kind == "share" else f"(ugc:{encoded_urn_inner})"

        headers = post_h  # already has Bearer + Linkedin-Version + Restli 2.0.0

        # Map LinkedIn metricType → our metrics_json field
        metric_map = [
            ("REACTION",   "likes"),
            ("COMMENT",    "comments"),
            ("RESHARE",    "shares"),
            ("IMPRESSION", "reach"),
        ]
        result = {"likes": 0, "comments": 0, "shares": 0, "reach": 0}
        got_any = False

        for query_type, out_field in metric_map:
            if _circuit["tripped"]:
                return None
            url = (
                "https://api.linkedin.com/rest/memberCreatorPostAnalytics"
                f"?q=entity&entity={entity_param}"
                f"&queryType={query_type}&aggregation=TOTAL"
            )
            try:
                r = requests.get(url, headers=headers, timeout=15)
                if r.status_code == 429:
                    _trip(f"memberCreatorPostAnalytics/{query_type} 429 on {post_urn}")
                    return None
                if r.status_code == 404:
                    # Post doesn't exist / not accessible under this scope
                    return None
                if r.status_code != 200:
                    logger.warning(
                        f"[LI_STATS] memberCreatorPostAnalytics/{query_type} "
                        f"failed ({r.status_code}) for {post_urn} body={r.text[:200]}"
                    )
                    continue
                data = r.json() or {}
                elements = data.get("elements") or []
                if elements:
                    count_val = int(elements[0].get("count", 0) or 0)
                    result[out_field] = count_val
                    got_any = True
            except Exception as e:
                logger.warning(
                    f"[LI_STATS] memberCreatorPostAnalytics/{query_type} "
                    f"exception for {post_urn}: {e}"
                )
                continue

        if not got_any:
            return None
        logger.info(
            f"[LI_STATS] ✅ memberCreatorPostAnalytics OK for {post_urn} "
            f"likes={result['likes']} comments={result['comments']} "
            f"shares={result['shares']} reach={result['reach']}"
        )
        return result

    def fetch_social_actions_metrics(post_urn):
        """Fallback for posts that fail org-share stats lookup.
        socialActions usually still returns likes/comments for accessible posts.
        """
        from urllib.parse import quote

        if _circuit["tripped"]:
            return None

        encoded_post_urn = quote(post_urn, safe="")
        url = f"https://api.linkedin.com/rest/socialActions/{encoded_post_urn}"

        last_status = None
        last_body = None
        for hdr_label, hdr in (("promoted", post_h), ("no-proto", post_h_no_proto)):
            if _circuit["tripped"]:
                return None
            try:
                res = requests.get(url, headers=hdr)
                if res.status_code == 429:
                    _trip(f"/rest/socialActions {hdr_label} 429 on {post_urn}")
                    return None
                if res.status_code == 404:
                    # Post URN is dead — don't try the other header variant.
                    return None
                if res.status_code != 200:
                    last_status = res.status_code
                    last_body = res.text[:200]
                    logger.warning(
                        f"LinkedIn socialActions[{hdr_label}] failed "
                        f"({res.status_code}) for {post_urn} body={last_body}"
                    )
                    continue
                d = res.json() or {}
                likes_summary = d.get("likesSummary") or {}
                comments_summary = d.get("commentsSummary") or {}
                total_counts = d.get("totalSocialActivityCounts") or {}

                likes = (
                    likes_summary.get("totalLikes")
                    or total_counts.get("numLikes")
                    or total_counts.get("totalLikes")
                    or 0
                )
                comments = (
                    comments_summary.get("aggregatedTotalComments")
                    or comments_summary.get("totalComments")
                    or total_counts.get("numComments")
                    or total_counts.get("totalComments")
                    or 0
                )
                shares = (
                    total_counts.get("numShares")
                    or total_counts.get("totalShares")
                    or 0
                )

                result = {
                    "likes": int(likes or 0),
                    "comments": int(comments or 0),
                    "shares": int(shares or 0),
                    "reach": 0,
                }
                logger.info(
                    f"LinkedIn socialActions[{hdr_label}] OK for {post_urn} "
                    f"likes={result['likes']} comments={result['comments']} shares={result['shares']}"
                )
                return result
            except Exception as e:
                logger.warning(
                    f"LinkedIn socialActions[{hdr_label}] exception for {post_urn}: {e}"
                )
                continue

        logger.warning(
            f"LinkedIn socialActions gave up for {post_urn} — "
            f"last_status={last_status} last_body={last_body}"
        )
        return None

    def fetch_linkedin_post_metrics(p):
        m_data = {"likes": 0, "comments": 0, "shares": 0, "reach": 0, "engagement": 0, "id": p.published_post_id, "platform": "linkedin"}
        try:
            # Circuit breaker: if we've already hit 429 in this sync run, do
            # NOT fire any more LinkedIn calls for the remaining posts.
            if _circuit["tripped"]:
                return m_data

            post_urn = normalize_linkedin_post_urn(p.native_post_id)
            if not post_urn:
                return m_data

            org_stats_ok = False
            if "organization" in acc_id:
                org_urn = acc_id if acc_id.startswith("urn:li:organization:") else f"urn:li:organization:{acc_id}"

                from urllib.parse import quote
                # IMPORTANT — LinkedIn versioned API (Linkedin-Version >= 202403)
                # uses Restli 2.0 protocol. The legacy `?shares[0]=...` array
                # syntax is Restli 1.0 and is REJECTED with 400 "Invalid param"
                # under 2.0. Restli 2.0 wants `?shares=List(<encoded-urn>)`.
                #
                # safe='' is critical here: the URN's colons (`:`) MUST be
                # percent-encoded inside the List(...) wrapper, otherwise
                # LinkedIn parses the path component wrong.
                encoded_post_urn = quote(post_urn, safe="")
                encoded_org_urn  = quote(org_urn,  safe="")
                u = (
                    f"https://api.linkedin.com/rest/organizationalEntityShareStatistics"
                    f"?q=organizationalEntity"
                    f"&organizationalEntity={encoded_org_urn}"
                    f"&shares=List({encoded_post_urn})"
                )
                res = requests.get(u, headers=post_h)
                if res.status_code == 429:
                    _trip(f"org-stats primary 429 on {post_urn}")
                    return m_data
                if res.status_code == 404:
                    # Post URN is dead — skip ALL fallbacks for this post.
                    return m_data
                if res.status_code != 200:
                    logger.warning(
                        f"LinkedIn post-stats primary fetch failed ({res.status_code}) for "
                        f"{post_urn} — retrying without protocol-draft header. body={res.text[:200]}"
                    )
                    res = requests.get(u, headers=post_h_no_proto)
                    if res.status_code == 429:
                        _trip(f"org-stats fallback 429 on {post_urn}")
                        return m_data
                    if res.status_code == 404:
                        return m_data

                if res.status_code == 200:
                    elements = res.json().get("elements", [])
                    if elements:
                        e = elements[0].get("totalShareStatistics", {})
                        m_data["likes"] = int(e.get("likeCount", 0) or e.get("like_count", 0))
                        m_data["comments"] = int(e.get("commentCount", 0) or e.get("comment_count", 0))
                        m_data["shares"] = int(e.get("shareCount", 0) or e.get("share_count", 0))
                        m_data["reach"] = int(e.get("uniqueImpressionsCount", 0) or e.get("impressionCount", 0))
                        org_stats_ok = True
                else:
                    logger.warning(
                        f"LinkedIn post-stats fallback also failed ({res.status_code}) for "
                        f"{post_urn}. body={res.text[:200]}"
                    )

            # Fallback for org-stats misses/rejections and member-owned posts.
            #
            # For PERSONAL posts, try the NEW memberCreatorPostAnalytics
            # endpoint FIRST — it lives in a separate resource-throttle
            # bucket from /v2/socialActions and /rest/socialActions, so it
            # can still return 200 when those are day-capped.
            #
            # Then fall through to the legacy paths for cases where the
            # new endpoint doesn't cover the post.
            if not org_stats_ok:
                is_org_account = "organization" in acc_id
                social_m = None
                if not is_org_account:
                    social_m = fetch_member_creator_post_analytics(post_urn)
                if not social_m:
                    social_m = fetch_social_actions_metrics(post_urn)
                if not social_m:
                    social_m = fetch_v2_social_counts(post_urn)
                if social_m:
                    m_data["likes"] = max(m_data["likes"], social_m["likes"])
                    m_data["comments"] = max(m_data["comments"], social_m["comments"])
                    m_data["shares"] = max(m_data["shares"], social_m["shares"])
                    m_data["reach"] = max(m_data["reach"], social_m["reach"])

            m_data["engagement"] = m_data["likes"] + m_data["comments"] + m_data["shares"]
            return m_data
        except Exception as e:
            logger.exception(f"LinkedIn post-stats exception for post={p.native_post_id}: {e}")
            return m_data

    if post_map:
        # De-duplicate by native post URN so repeated rows don't trigger
        # repeated LinkedIn warnings for the same post in a single sync pass.
        unique_posts = {}
        for p in post_map:
            key = normalize_linkedin_post_urn(p.native_post_id) or f"row:{p.id}"
            if key not in unique_posts:
                unique_posts[key] = p

        # Sequential (not ThreadPoolExecutor(10)) — LinkedIn's per-second
        # throttle is much tighter than the daily bucket. 10 workers × 6
        # calls each burst to 60 concurrent requests which trips per-second
        # limits and cascades into daily-cap 429s. Sequential + circuit
        # breaker means one call at a time and immediate bail on first 429.
        metrics_by_key = {}
        skipped_keys: set = set()
        for key, post_row in unique_posts.items():
            if _circuit["tripped"]:
                skipped_keys.add(key)
                continue
            metrics_by_key[key] = fetch_linkedin_post_metrics(post_row)

        _fetched_ct = len(metrics_by_key)
        _skipped_ct = len(skipped_keys)
        if _skipped_ct:
            logger.warning(
                f"[LI_STATS] fetched metrics for {_fetched_ct}/{len(unique_posts)} posts. "
                f"{_skipped_ct} skipped due to circuit-breaker. "
                f"Previously-stored metrics for skipped posts will NOT be overwritten."
            )

        # IMPORTANT — only emit detailed_posts entries for posts we ACTUALLY
        # fetched. Skipped posts (circuit tripped) are omitted so the caller
        # doesn't overwrite the DB's existing metrics with zeros.
        detailed_posts = []
        for p in post_map:
            key = normalize_linkedin_post_urn(p.native_post_id) or f"row:{p.id}"
            if key in skipped_keys:
                continue  # circuit-broken → leave DB row untouched
            m = metrics_by_key.get(key, {"likes": 0, "comments": 0, "shares": 0, "reach": 0})
            detailed_posts.append({
                "id": p.published_post_id,
                "platform": "linkedin",
                "likes": int(m.get("likes", 0) or 0),
                "comments": int(m.get("comments", 0) or 0),
                "shares": int(m.get("shares", 0) or 0),
                "reach": int(m.get("reach", 0) or 0),
                "engagement": int(m.get("likes", 0) or 0) + int(m.get("comments", 0) or 0) + int(m.get("shares", 0) or 0),
            })

    logger.info(f"LinkedIn Sync Complete: {len(detailed_posts)} posts for {account.name}")
    return stats, detailed_posts

def get_twitter_stats(account, post_map):
    """Fetch Twitter Profile and Post stats via OAuth 1.0a."""
    token = account.token
    token_secret = account.token_secret
    
    stats = {"followers": 0}
    detailed_posts = []
    
    use_oauth1 = bool(TWITTER_API_KEY and TWITTER_API_SECRET and token_secret)
    if not use_oauth1:
        logger.info(
            "Twitter stats using OAuth2 bearer fallback "
            f"for account_id={account.account_id} "
            f"(api_key={'set' if TWITTER_API_KEY else 'MISSING'}, "
            f"api_secret={'set' if TWITTER_API_SECRET else 'MISSING'}, "
            f"token_secret={'set' if token_secret else 'MISSING'})."
        )

    try:
        twitter = None
        headers = None
        if use_oauth1:
            twitter = OAuth1Session(
                TWITTER_API_KEY,
                client_secret=TWITTER_API_SECRET,
                resource_owner_key=token,
                resource_owner_secret=token_secret,
            )
        else:
            headers = {"Authorization": f"Bearer {token}"}

        # 1. User metrics
        if use_oauth1:
            res = twitter.get("https://api.twitter.com/2/users/me", params={"user.fields": "public_metrics"})
        else:
            res = requests.get(
                "https://api.twitter.com/2/users/me",
                params={"user.fields": "public_metrics"},
                headers=headers,
                timeout=10,
            )
        logger.info(f"Twitter User Stats Res ({res.status_code}) body={res.text[:200]}")
        if res.status_code == 200:
            metrics = res.json().get("data", {}).get("public_metrics", {})
            stats["followers"] = metrics.get("followers_count", 0)
        elif res.status_code in (401, 403):
            logger.warning(
                f"Twitter 1.0a auth rejected ({res.status_code}). Token for "
                f"{account.name!r} may be revoked — user should reconnect X."
            )
        elif res.status_code == 429:
            logger.warning("Twitter rate-limited on /2/users/me — Free tier is 1 call per 15m per endpoint.")

        # 2. Post metrics
        if post_map:
            # Twitter v2 /2/tweets has TWO hard constraints that the previous
            # implementation violated:
            #   (a) IDs must be UNIQUE — duplicates → 400 with
            #       "ids" parameter validation error.
            #   (b) Max 100 IDs per call — request with 101+ → 400.
            # If the same tweet was published from multiple campaigns the
            # PublishedPostPlatform table has the native_post_id repeated;
            # we collapse those duplicates here, then chunk into sub-100
            # batches and merge the responses.
            unique_ids = []
            seen = set()
            for p in post_map:
                pid = str(p.native_post_id) if p.native_post_id else None
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                unique_ids.append(pid)

            tweet_data = {}
            CHUNK = 100
            chunk_failure = False
            for i in range(0, len(unique_ids), CHUNK):
                batch = unique_ids[i:i + CHUNK]
                ids_param = ",".join(batch)
                logger.info(
                    f"Twitter Post Stats fetching {len(batch)} ids "
                    f"(batch {i // CHUNK + 1}/{(len(unique_ids) + CHUNK - 1) // CHUNK})"
                )
                if use_oauth1:
                    res = twitter.get(
                        f"https://api.twitter.com/2/tweets?ids={ids_param}&tweet.fields=public_metrics"
                    )
                else:
                    res = requests.get(
                        "https://api.twitter.com/2/tweets",
                        params={"ids": ids_param, "tweet.fields": "public_metrics"},
                        headers=headers,
                        timeout=15,
                    )
                logger.info(f"Twitter Post Stats Res ({res.status_code}) body={res.text[:300]}")
                if res.status_code == 429:
                    logger.warning(
                        "Twitter /2/tweets hit rate limit (Free tier = 1 req / 15min). "
                        "Per-tweet metrics will stay at 0 until next window. "
                        "Upgrade to Basic tier ($100/mo) for usable polling."
                    )
                    chunk_failure = True
                    break
                if res.status_code != 200:
                    chunk_failure = True
                    continue
                for t in res.json().get("data", []):
                    tweet_data[str(t["id"])] = t["public_metrics"]

            for p in post_map:
                m = tweet_data.get(str(p.native_post_id), {})
                m_data = {
                    "id": p.published_post_id,
                    "platform": "twitter",
                    "likes":    m.get("like_count", 0),
                    "comments": m.get("reply_count", 0),
                    "shares":   m.get("retweet_count", 0),
                    "reach":    m.get("impression_count", 0),
                }
                m_data["engagement"] = m_data["likes"] + m_data["comments"] + m_data["shares"]
                detailed_posts.append(m_data)
    except Exception as e:
        logger.error(f"Twitter Sync Error: {str(e)}")
        
    logger.info(f"Twitter Sync Complete: {len(detailed_posts)} posts for {account.name}")
    return stats, detailed_posts

def get_meta_stats(account, post_map):
    """Fetch Facebook/Instagram Page and Post stats with fallbacks."""
    token = account.token
    acc_id = account.account_id
    platform = account.platform
    stats = {"followers": 0, "reach": 0, "engagement": 0}
    detailed_posts = []
    
    # Debug: Check actual token scopes using App Token
    try:
        import os
        fb_app_id = os.getenv("FACEBOOK_APP_ID")
        fb_app_secret = os.getenv("FACEBOOK_APP_SECRET")
        app_token = f"{fb_app_id}|{fb_app_secret}"
        debug_res = requests.get(f"https://graph.facebook.com/debug_token?input_token={token}&access_token={app_token}")
        if debug_res.status_code == 200:
            d_json = debug_res.json().get("data", {})
            scopes = d_json.get("scopes", [])
            logger.info(f"Meta Token Scopes for {platform}: {scopes}")
            if d_json.get("error"):
                logger.error(f"Meta Token Error: {d_json.get('error')}")
    except Exception as e:
        logger.error(f"Meta Debug Token Call Failed: {str(e)}")
    # 1. Profile & Page-level Stats
    try:
        # A. Basic fields
        fields = "followers_count" if platform == "instagram" else "fan_count,name"
        res = requests.get(f"https://graph.facebook.com/v19.0/{acc_id}?fields={fields}&access_token={token}")
        if res.status_code == 200:
            data = res.json()
            stats["followers"] = data.get("followers_count") or data.get("fan_count") or 0
            logger.info(f"Meta Profile Data for {platform} sync successful.")
        # B. Page-level Insights (Total Page Reach/Views) - Requires 'read_insights'
        # Requesting metrics individually to avoid #100 breaking the entire call
        if platform == "facebook":
            for m_name in ["page_posts_impressions", "page_views_total", "page_fan_adds_unique"]:
                try:
                    i_res = requests.get(f"https://graph.facebook.com/v19.0/{acc_id}/insights?metric={m_name}&period=day&access_token={token}")
                    if i_res.status_code == 200:
                        i_data = i_res.json().get("data", [])
                        if i_data and i_data[0].get("values"):
                            val = i_data[0]["values"][-1]["value"]
                            if m_name == "page_views_total": stats["reach"] = int(val)
                except: pass
        else:
            # Instagram Business Insights - Requires 'instagram_manage_insights'
            try:
                # Based on terminal error, 'views' requires metric_type=total_value
                i_res = requests.get(f"https://graph.facebook.com/v19.0/{acc_id}/insights?metric=reach,views&period=day&metric_type=total_value&access_token={token}")
                if i_res.status_code == 200:
                    i_data = i_res.json().get("data", [])
                    raw_i = {m["name"]: m["values"][-1]["value"] for m in i_data if m.get("values")}
                    stats["reach"] = int(raw_i.get("reach") or raw_i.get("views") or 0)
                else:
                    logger.warning(f"IG Insights Page Error: {i_res.status_code}")
            except: pass
    except Exception as e:
        logger.error(f"Meta Profile/Page Sync Error: {str(e)}")
    
    # 2. Post Stats (Optimized Parallel Fetch)
    m_list_fb = "post_impressions"
    m_list_ig = "reach,total_interactions"

    def fetch_post_metrics(p):
        m_data = {"likes": 0, "comments": 0, "shares": 0, "reach": 0, "engagement": 0, "id": p.published_post_id, "platform": platform}
        try:
            insight_id = p.native_post_id
            m_list = m_list_fb if platform == "facebook" else m_list_ig
            if platform == "facebook" and "_" not in str(insight_id):
                insight_id = f"{acc_id}_{insight_id}"

            # Insights
            res = requests.get(f"https://graph.facebook.com/v19.0/{insight_id}/insights?metric={m_list}&access_token={token}", timeout=5)
            if res.status_code == 200:
                raw_m = {m["name"]: m["values"][0]["value"] for m in res.json().get("data", [])}
                m_data["reach"] = int(raw_m.get("post_impressions_unique") or raw_m.get("post_impressions") or raw_m.get("reach") or 0)
                if platform == "instagram": m_data["engagement"] = int(raw_m.get("total_interactions") or 0)

            # Standard Metrics
            if platform == "instagram":
                obj_res = requests.get(f"https://graph.facebook.com/v19.0/{p.native_post_id}?fields=like_count,comments_count&access_token={token}", timeout=5)
                if obj_res.status_code == 200:
                    d = obj_res.json()
                    m_data["likes"], m_data["comments"] = int(d.get("like_count", 0)), int(d.get("comments_count", 0))
            else:
                l_r = requests.get(f"https://graph.facebook.com/v19.0/{p.native_post_id}/likes?summary=true&limit=0&access_token={token}", timeout=5).json()
                m_data["likes"] = int(l_r.get("summary", {}).get("total_count", 0))
                c_r = requests.get(f"https://graph.facebook.com/v19.0/{p.native_post_id}/comments?summary=true&limit=0&access_token={token}", timeout=5).json()
                m_data["comments"] = int(c_r.get("summary", {}).get("total_count", 0))
                s_r = requests.get(f"https://graph.facebook.com/v19.0/{p.native_post_id}?fields=shares&access_token={token}", timeout=5).json()
                m_data["shares"] = int(s_r.get("shares", {}).get("count", 0))
            
            m_data["engagement"] = m_data["likes"] + m_data["comments"] + m_data["shares"]
            return m_data
        except Exception:
            return m_data

    if post_map:
        with ThreadPoolExecutor(max_workers=min(len(post_map), 10)) as executor:
            detailed_posts = list(executor.map(fetch_post_metrics, post_map))
    
    logger.info(f"Meta Sync Complete: {len(detailed_posts)} posts for {account.name}")
    return stats, detailed_posts

def sync_account_analytics(db: Session, account: SocialAccount, force=False):
    """Fetch and update stats for a specific social account."""
    if not force and account.last_analytics_sync and (datetime.utcnow() - account.last_analytics_sync) < timedelta(hours=1):
        return

    def _linkedin_account_id_candidates(acc_id: str):
        raw = str(acc_id or "").strip()
        if not raw:
            return []
        out = {raw}
        if raw.startswith("urn:li:organization:"):
            out.add(raw.split(":")[-1])
        elif raw.isdigit():
            out.add(f"urn:li:organization:{raw}")
        return list(out)

    # SCOPE the sync to the CURRENT user's own posts, not every post ever
    # published to this LinkedIn page/URN.
    #
    # LinkedIn org pages are shared property: two Pipelyt users can both
    # connect the same NEUZEN AI page. If we query only by account_id, a
    # brand-new user 65 who connects a page that user 15 has been publishing
    # to for months instantly inherits ALL of user 15's 331 posts and
    # syncs them under user 65's token — burning through user 65's LinkedIn
    # daily throttle for posts they never published.
    #
    # Fix: JOIN through PublishedPost and filter on user_id so each user's
    # sync only touches posts THEY (or their team-scoped colleague) published.
    # A fresh Pipelyt account with 0 posts → 0 sync calls → no rate limit.
    _sync_owner_ids = [
        x for x in [account.user_id, account.assigned_to_user_id] if x
    ]
    logger.info(
        f"[SYNC_SCOPE] platform={account.platform} account_id={account.account_id!r} "
        f"scoped_to_user_ids={_sync_owner_ids}"
    )

    if account.platform == 'linkedin':
        acc_candidates = _linkedin_account_id_candidates(account.account_id)
        _q = db.query(PublishedPostPlatform).join(
            PublishedPost, PublishedPost.id == PublishedPostPlatform.published_post_id
        ).filter(
            PublishedPostPlatform.platform == 'linkedin',
            PublishedPostPlatform.account_id.in_(acc_candidates),
        )
        if _sync_owner_ids:
            _q = _q.filter(PublishedPost.user_id.in_(_sync_owner_ids))
        post_map = _q.all()
    else:
        _q = db.query(PublishedPostPlatform).join(
            PublishedPost, PublishedPost.id == PublishedPostPlatform.published_post_id
        ).filter(
            PublishedPostPlatform.platform == account.platform,
            PublishedPostPlatform.account_id == account.account_id,
        )
        if _sync_owner_ids:
            _q = _q.filter(PublishedPost.user_id.in_(_sync_owner_ids))
        post_map = _q.all()

    logger.info(
        f"[SYNC_SCOPE] platform={account.platform} account_id={account.account_id!r} "
        f"→ {len(post_map)} post(s) belong to user(s) {_sync_owner_ids} "
        f"(before this fix the query would have returned ALL posts ever "
        f"published to this account_id by any Pipelyt user)"
    )

    # Staging/prod parity fallback: if account_id formatting drifted
    # (or account rows were reconnected) and exact mapping misses, use a
    # constrained owner-scope + platform fallback so sync still updates.
    if not post_map:
        owner_ids = [x for x in [account.user_id, account.assigned_to_user_id] if x]
        if owner_ids:
            post_map = (
                db.query(PublishedPostPlatform)
                .join(PublishedPost, PublishedPost.id == PublishedPostPlatform.published_post_id)
                .filter(
                    PublishedPostPlatform.platform == account.platform,
                    PublishedPost.user_id.in_(owner_ids),
                )
                .all()
            )
            if post_map:
                logger.warning(
                    "Analytics sync used owner-scope fallback mapping for "
                    f"platform={account.platform} account_id={account.account_id} "
                    f"rows={len(post_map)}"
                )
    stats, detailed_posts = {}, []
    
    if account.platform == 'linkedin':
        stats, detailed_posts = get_linkedin_stats(account, post_map)
    elif account.platform == 'twitter':
        stats, detailed_posts = get_twitter_stats(account, post_map)
    elif account.platform in ['facebook', 'instagram']:
        stats, detailed_posts = get_meta_stats(account, post_map)
    elif account.platform == 'youtube':
        # YouTube needs the DB session to refresh its access_token, so unlike
        # the other branches we pass `db` through. The signature matches the
        # others otherwise — returns (stats={"followers": N, …}, detailed_posts).
        from services.youtube_analytics import get_youtube_stats
        stats, detailed_posts = get_youtube_stats(account, post_map, db)
    elif account.platform == 'tiktok':
        # TikTok mirrors YouTube's signature exactly (refreshes its own access
        # token from `db`, returns the same shape). May return zeros until the
        # app passes TikTok Content Sharing audit — sandbox apps see a 403
        # on some endpoints and we just log + skip the affected metric.
        from services.tiktok_analytics import get_tiktok_stats
        stats, detailed_posts = get_tiktok_stats(account, post_map, db)

    if "followers" in stats and stats["followers"] is not None:
        new_count = int(stats["followers"] or 0)
        # Skip updating if we got 0 but had a higher value before (likely a temp sync failure/rate limit)
        if new_count == 0 and (account.follower_count or 0) > 0:
            logger.warning(f"Meta/LinkedIn Sync returned 0 followers for {account.name} (ID: {account.id}). Keeping last known value: {account.follower_count}")
        else:
            account.follower_count = new_count
        
        # Seed baseline on first real sync (initial_follower_count will be 0
        # right after account creation, then gets set to the first non-zero
        # follower count we observe). Never overwritten on subsequent syncs.
        if (not account.initial_follower_count) and new_count > 0:
            account.initial_follower_count = new_count
            if not account.initial_connected_at:
                account.initial_connected_at = datetime.utcnow()
    account.last_analytics_sync = datetime.utcnow()
    
    for dp in detailed_posts:
        platform_post = db.query(PublishedPostPlatform).filter(
            PublishedPostPlatform.published_post_id == dp["id"],
            PublishedPostPlatform.platform == dp["platform"],
            PublishedPostPlatform.account_id == account.account_id,
        ).first()
        if not platform_post and account.platform == 'linkedin':
            # Retry with normalized LinkedIn account id forms.
            acc_candidates = _linkedin_account_id_candidates(account.account_id)
            if acc_candidates:
                platform_post = db.query(PublishedPostPlatform).filter(
                    PublishedPostPlatform.published_post_id == dp["id"],
                    PublishedPostPlatform.platform == dp["platform"],
                    PublishedPostPlatform.account_id.in_(acc_candidates),
                ).first()
        if not platform_post:
            # Final fallback: same post + platform row, even if account_id mismatched.
            platform_post = db.query(PublishedPostPlatform).filter(
                PublishedPostPlatform.published_post_id == dp["id"],
                PublishedPostPlatform.platform == dp["platform"],
            ).first()
        if platform_post:
            platform_post.metrics_json = dp
            platform_post.last_synced_at = datetime.utcnow()
    db.commit()

@router.post("/analytics/sync")
async def sync_analytics(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Refresh analytics. The account-level pass (followers + per-post
    metrics + UserAnalyticsSnapshot) runs synchronously and the response
    returns when those numbers are committed. Sentiment analysis is
    queued as a background task so it doesn't block the foreground UX —
    the next time the user opens a post, the latest sentiment is there.

    Earlier behaviour ran sentiment inside the response (blocked ~5 min)
    AND synced platforms sequentially (another ~3 min). Together that
    made the UI spinner hang for 7+ minutes. With parallel platform sync
    + background sentiment, the foreground response is ~30–90 s."""
    check_feature(current_user, "analytics")

    from sqlalchemy import or_ as _or_
    visible_ids = get_team_scope_user_ids(db, current_user)
    accounts_raw = db.query(SocialAccount).filter(
        _or_(
            SocialAccount.user_id.in_(visible_ids),
            SocialAccount.assigned_to_user_id.in_(visible_ids),
        )
    ).all()
    # Dedupe by id. The OR query can yield the same account twice when an
    # admin's own account satisfies BOTH conditions (user_id == admin AND
    # assigned_to_user_id == admin), or when stale duplicate rows linger
    # from a re-connect. Without this, total_f would double during sync.
    seen_ids = set()
    accounts = []
    for a in accounts_raw:
        if a.id in seen_ids:
            continue
        seen_ids.add(a.id)
        accounts.append(a)
    account_ids = [acc.id for acc in accounts]
    if not account_ids:
        return {"status": "No accounts connected to sync"}

    from fastapi.concurrency import run_in_threadpool
    try:
        await run_in_threadpool(sync_all_user_accounts, current_user.id, account_ids)
    except Exception as e:
        logger.error(f"Sync analytics failed for user {current_user.id}: {e}")
        return {"status": "Sync partially failed", "error": str(e)}

    # Sentiment refresh — fire and forget. Will populate metrics_json["sentiment"]
    # for posts with comments without holding the response open.
    background_tasks.add_task(refresh_sentiments_for_user, current_user.id)

    return {"status": "Sync complete", "sentiment_refresh": "queued"}

def _fetch_comments_for_platform_post(account: SocialAccount, native_id: str):
    """Wrapper so sync loop can pull comments for any platform without caring
    about adapter differences. Returns (comments_list, error_or_None)."""
    try:
        if account.platform == "facebook":
            return fetch_facebook_comments(account.token, native_id)
        if account.platform == "instagram":
            # Instagram comments fetching is temporarily put on hold
            return [], None
        if account.platform == "linkedin":
            return fetch_linkedin_org_comments(account.token, native_id, account.account_id)
        if account.platform == "twitter":
            return fetch_twitter_comments(
                TWITTER_API_KEY, TWITTER_API_SECRET,
                account.token, account.token_secret, native_id,
            )
    except Exception as e:
        return [], f"fetch error: {e}"
    return [], "unsupported platform"


def _run_post_sentiment(db: Session, platform_post: PublishedPostPlatform,
                        account: SocialAccount, stale_after_hours: int = 24) -> None:
    """Run sentiment analysis for one PublishedPostPlatform and stash the
    result into `metrics_json["sentiment"]`. Skips posts with < 1 comment,
    unsupported platforms, or a fresh cached result (<24h old). Silent on
    errors — sentiment is best-effort and shouldn't break the sync."""
    try:
        m = dict(platform_post.metrics_json or {})
        # Fresh cache? skip.
        cached = m.get("sentiment") or {}
        if cached.get("analyzed_at"):
            try:
                last = datetime.fromisoformat(cached["analyzed_at"].rstrip("Z"))
                if (datetime.utcnow() - last) < timedelta(hours=stale_after_hours):
                    return
            except Exception:
                pass
        if int(m.get("comments") or 0) < 1:
            return

        comments, err = _fetch_comments_for_platform_post(account, platform_post.native_post_id)
        if err or not comments:
            return

        context = None
        if platform_post.published_post:
            context = platform_post.published_post.content
        analysis = analyze_comments_sentiment(comments, post_context=context)
        if not isinstance(analysis, dict) or "error" in analysis:
            return

        m["sentiment"] = {
            "overall_score": analysis.get("overall_score"),
            "overall_sentiment": analysis.get("overall_sentiment"),
            "overall_summary": analysis.get("overall_summary"),
            "top_insight": analysis.get("top_insight"),
            "sentiment_counts": analysis.get("sentiment_counts"),
            "analyzed_comments": analysis.get("analyzed_comments"),
            "analyzed_at": datetime.utcnow().isoformat() + "Z",
            "comment_count": len(comments),
        }
        platform_post.metrics_json = m
    except Exception as e:
        logger.warning(
            f"Sentiment analysis skipped for {platform_post.platform}/"
            f"{platform_post.native_post_id}: {e}"
        )


def sync_all_user_accounts(user_id: int, account_ids: list):
    db = SessionLocal()
    try:
        # PARALLEL account sync — was sequential before, which made the
        # whole /analytics/sync endpoint block for ~5 minutes (LinkedIn
        # 15-30s + Facebook 60-90s + Twitter rate-limit waits + Instagram
        # 60-90s, all back-to-back). Running each account's sync in its
        # own thread parallelizes the per-platform API calls so the
        # endpoint returns in roughly the slowest single platform's time.
        def _sync_one(acc_id):
            sw_db = SessionLocal()
            try:
                sw_acc = sw_db.query(SocialAccount).filter(SocialAccount.id == acc_id).first()
                if sw_acc:
                    sync_account_analytics(sw_db, sw_acc, force=True)
                    return acc_id, sw_acc.account_id, sw_acc.platform, int(sw_acc.follower_count or 0)
            except Exception as e:
                logger.warning(f"Sync failed for account {acc_id}: {e}")
            finally:
                sw_db.close()
            return acc_id, None, None, 0

        total_f, total_e, total_r = 0, 0, 0
        platform_counts = {}
        account_by_id = {}
        # Cap at 4 workers so we don't fan out so wide that we hit per-IP
        # rate limits on Facebook / LinkedIn — typical connection counts
        # are 1–5 platforms per user, so 4 is plenty.
        with ThreadPoolExecutor(max_workers=min(len(account_ids) or 1, 4)) as pool:
            for acc_id, native_id, platform, f_count in pool.map(_sync_one, account_ids):
                if native_id and platform is not None:
                    total_f += f_count
                    platform_counts[f"{platform}_{acc_id}"] = f_count
                    # Reload in *this* db session for the sentiment pass below.
                    refreshed = db.query(SocialAccount).filter(SocialAccount.id == acc_id).first()
                    if refreshed:
                        account_by_id[refreshed.account_id] = refreshed
        logger.info(f"Sync: {len(account_by_id)} accounts refreshed for user {user_id}")

        all_posts = db.query(PublishedPostPlatform).join(PublishedPost).filter(PublishedPost.user_id == user_id).all()
        total_l, total_c, total_s = 0, 0, 0
        for p in all_posts:
            m = p.metrics_json or {}
            total_e += int(m.get("engagement") or 0)
            total_r += int(m.get("reach") or 0)
            total_l += int(m.get("likes") or 0)
            total_c += int(m.get("comments") or 0)
            total_s += int(m.get("shares") or 0)

        today = datetime.utcnow().date()
        # Hourly Snapshot Logic (Apr 2026): Instead of daily, save snapshots per hour 
        # to support the "last 24 hours" dynamic X-axis in the frontend.
        from sqlalchemy import extract
        existing = db.query(UserAnalyticsSnapshot).filter(
            UserAnalyticsSnapshot.user_id == user_id,
            func.date(UserAnalyticsSnapshot.snapshot_date) == today,
            extract('hour', UserAnalyticsSnapshot.snapshot_date) == datetime.utcnow().hour
        ).first()

        # Follower Drop Guard (Issue 2): If the current sync aggregated to 0 followers
        # but we have historical data, carry forward the last non-zero total to
        # prevent massive visual dips in the line graph.
        if total_f == 0:
            last_snap = db.query(UserAnalyticsSnapshot).filter(
                UserAnalyticsSnapshot.user_id == user_id,
                UserAnalyticsSnapshot.total_followers > 0
            ).order_by(UserAnalyticsSnapshot.snapshot_date.desc()).first()
            if last_snap:
                logger.warning(f"Sync for user {user_id} returned 0 total followers. Carrying forward {last_snap.total_followers} from {last_snap.snapshot_date}")
                total_f = last_snap.total_followers

        if existing:
            existing.total_followers, existing.total_engagement, existing.total_reach = total_f, total_e, total_r
            existing.total_likes, existing.total_comments, existing.total_shares = total_l, total_c, total_s
            existing.platform_breakdown = platform_counts
        else:
            db.add(UserAnalyticsSnapshot(
                user_id=user_id, total_followers=total_f, total_engagement=total_e, total_reach=total_r,
                total_likes=total_l, total_comments=total_c, total_shares=total_s,
                platform_breakdown=platform_counts
            ))
        db.commit()

        # Sentiment work is moved out — see _refresh_sentiments_async below.
        # /analytics/sync now returns as soon as the account-level snapshot
        # is committed (~30–60s with the parallel sync above), and a
        # background thread refreshes per-post sentiment without blocking
        # the response.
    except Exception as e:
        logger.error(f"Global sync task failed: {e}")
    finally:
        db.close()


def refresh_sentiments_for_user(user_id: int):
    """Background pass: parallel sentiment refresh for every PublishedPostPlatform
    belonging to `user_id`. Designed to be fire-and-forgotten by /analytics/sync
    so the foreground request returns quickly."""
    db = SessionLocal()
    try:
        all_posts = (
            db.query(PublishedPostPlatform)
              .join(PublishedPost)
              .filter(PublishedPost.user_id == user_id)
              .all()
        )
        # Map each post's account_id → SocialAccount(id) so the worker can
        # reload the right token in its own session.
        account_lookup = {}
        accounts = db.query(SocialAccount).filter(SocialAccount.user_id == user_id).all()
        for a in accounts:
            account_lookup[a.account_id] = a.id

        def _w(p_id, acc_id):
            sw_db = SessionLocal()
            try:
                sw_p = sw_db.query(PublishedPostPlatform).filter(PublishedPostPlatform.id == p_id).first()
                sw_acc = sw_db.query(SocialAccount).filter(SocialAccount.id == acc_id).first()
                if sw_p and sw_acc:
                    _run_post_sentiment(sw_db, sw_p, sw_acc)
                    sw_db.commit()
            except Exception as e:
                logger.warning(f"Sentiment worker failed for post {p_id}: {e}")
            finally:
                sw_db.close()

        tasks = [(p.id, account_lookup[p.account_id]) for p in all_posts if p.account_id in account_lookup]
        if tasks:
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda t: _w(t[0], t[1]), tasks))
            logger.info(f"Background sentiment refresh: {len(tasks)} posts done for user {user_id}")
    except Exception as e:
        logger.error(f"Background sentiment refresh failed for user {user_id}: {e}")
    finally:
        db.close()


@router.get("/analytics/summary")
async def get_analytics_summary(
    time_period: str = '7d', start_date: str = None, end_date: str = None,
    dna_product_id: str = None,
    # New (Apr 2026): multi-brand csv — narrows posts + visible-members to
    # those assigned at least one of the selected brands. Takes priority
    # over `dna_product_id` (single) when both are set. Admin only.
    dna_product_ids: str = None,
    member_user_ids: str = None,  # admin-only: csv of team-member ids to include
    platform: str = 'all',        # 'linkedin' | 'twitter' | 'facebook' | 'instagram' | 'all'
    # Admin-only profile filters: narrow the "team scope" to members whose
    # stored profile matches. Each is optional; combining them ANDs them.
    # Filters run BEFORE `member_user_ids`, so if both are given the member
    # csv further narrows the profile-matched set.
    filter_company: str = None,   # matches User.member_company_name (exact, case-insensitive)
    filter_country: str = None,   # matches User.country (ISO-2)
    filter_state: str = None,     # matches User.state (ISO-2 subdivision)
    filter_city: str = None,      # matches User.city
    filter_pin_code: str = None,  # matches User.pin_code
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    # 1. Feature Check
    check_feature(current_user, "analytics")

    now = datetime.utcnow()
    # Range Logic — frontend sends one of: 24h, 7d, 30d, 90d, 1y, all, custom.
    # `all` reports everything since 2020-01-01 (effectively all-time for any
    # Pipelyt user). Each branch also computes a previous-period window for
    # delta arrows on KPI tiles.
    if time_period == '24h':
        since, prev_since = now - timedelta(hours=24), now - timedelta(hours=48)
    elif time_period == '30d':
        since, prev_since = now - timedelta(days=30), now - timedelta(days=60)
    elif time_period == '90d':
        since, prev_since = now - timedelta(days=90), now - timedelta(days=180)
    elif time_period == '1y':
        since, prev_since = now - timedelta(days=365), now - timedelta(days=730)
    elif time_period == 'all':
        since = datetime(2020, 1, 1)
        prev_since = since  # no meaningful previous period for "all-time"
    elif time_period == 'custom' and start_date and end_date:
        since, end = datetime.fromisoformat(start_date), datetime.fromisoformat(end_date)
        prev_since = since - (end - since)
    else:
        since, prev_since = now - timedelta(days=7), now - timedelta(days=14)

    # KPI/Chart consistency fix — for multi-day windows the chart's
    # `history_since` is later snapped to start-of-day for a clean x-axis,
    # which previously made the chart include ~hours of extra posts the
    # KPI summary did not (KPI used the exact-moment `since`). Aligning
    # `since` AND `prev_since` to start-of-day here makes the KPI tiles
    # sum exactly the same posts the chart line plots. 24h keeps the
    # exact-hour boundary so it remains a true rolling 24h window.
    if time_period in ('7d', '30d', '90d', '1y'):
        since = since.replace(hour=0, minute=0, second=0, microsecond=0)
        prev_since = prev_since.replace(hour=0, minute=0, second=0, microsecond=0)

    # Team scope — admin sees self + all members' data; member sees own only.
    # Admin can also narrow the view to specific team members via
    # ?member_user_ids=1,2,3 (mutually-exclusive with the legacy DNA filter).
    visible_ids = get_team_scope_user_ids(db, current_user)
    if is_team_member(current_user):
        # Members can filter by one of their own assigned brands. Drop
        # invalid ids silently; the `member_user_ids` param is admin-only.
        from services.team_service import get_member_assigned_dna_ids
        allowed = set(get_member_assigned_dna_ids(current_user))
        if dna_product_id and dna_product_id not in allowed:
            dna_product_id = None
        member_user_ids = None
    else:
        # Admin: apply profile filters FIRST (company/country/state/city/pin)
        # to derive a set of matching team-member user_ids, then apply the
        # member_user_ids csv on top. A blank filter value means "don't
        # filter on that dimension" — only non-empty values narrow the set.
        profile_filters_active = any([
            filter_company, filter_country, filter_state,
            filter_city, filter_pin_code,
        ])
        if profile_filters_active:
            from sqlalchemy import func as _sqlfunc
            q = db.query(User.id).filter(User.team_owner_id == current_user.id)
            if filter_company:
                # Case-insensitive exact match on the external company name.
                q = q.filter(_sqlfunc.lower(User.member_company_name) == filter_company.strip().lower())
            if filter_country:
                q = q.filter(User.country == filter_country.strip())
            if filter_state:
                q = q.filter(User.state == filter_state.strip())
            if filter_city:
                q = q.filter(_sqlfunc.lower(User.city) == filter_city.strip().lower())
            if filter_pin_code:
                q = q.filter(User.pin_code == filter_pin_code.strip())
            matched = {int(r[0]) for r in q.all()}
            # When profile filters are set, we EXCLUDE the admin row — the
            # filter expresses "show only members in this segment". If no
            # member matches, visible_ids becomes an empty list and the
            # metrics below all resolve to zero, which is the correct signal
            # ("no members match your filter" rather than silently showing
            # the whole team).
            visible_ids = sorted(matched)

        # Apply the member multi-select filter. Validate each id is actually
        # one of their team members AND still in the profile-filtered set if
        # that's been applied. When a specific selection is made, we EXCLUDE
        # admin from visible_ids — so "just Alice" shows only Alice. Empty
        # selection still falls through to the (possibly profile-filtered)
        # scope from above.
        if member_user_ids:
            try:
                requested = [int(x) for x in member_user_ids.split(",") if x.strip()]
            except Exception:
                requested = []
            if requested:
                # Validate against team members AND the admin themselves. The
                # admin's own id is a legitimate filter value ("show only my
                # data") but the previous query joined only on
                # team_owner_id == current_user.id, which silently dropped
                # the admin and left the filter ineffective. Now we accept
                # current_user.id directly + any team member id.
                team_ids = {
                    int(r[0]) for r in db.query(User.id).filter(
                        User.team_owner_id == current_user.id,
                        User.id.in_(requested),
                    ).all()
                }
                valid_ids = set(team_ids)
                if int(current_user.id) in requested:
                    valid_ids.add(int(current_user.id))
                if profile_filters_active:
                    # Intersect with profile-matched ids so the csv can only
                    # narrow, never widen back to unfiltered members.
                    valid_ids = valid_ids & set(visible_ids)
                if valid_ids:
                    visible_ids = sorted(valid_ids)

    # Brand filter — multi-id (`dna_product_ids`) takes priority over legacy
    # single-id (`dna_product_id`). Both paths narrow visible_ids to the
    # admin + team members whose assignment list contains ANY of the brands.
    selected_brand_ids: list[str] = []
    if dna_product_ids:
        selected_brand_ids = [x.strip() for x in dna_product_ids.split(",") if x.strip()]
    elif dna_product_id:
        selected_brand_ids = [dna_product_id]

    if selected_brand_ids and not is_team_member(current_user):
        brand_members = db.query(User).filter(
            User.team_owner_id == current_user.id,
        ).all()
        keep = [int(current_user.id)]
        sel_set = set(selected_brand_ids)
        for m in brand_members:
            mids = set(
                (m.assigned_dna_product_ids or [])
                or ([m.assigned_dna_product_id] if m.assigned_dna_product_id else [])
            )
            if mids & sel_set:
                keep.append(int(m.id))
        # Intersect with any prior profile/member filter so brand narrows,
        # never widens.
        if profile_filters_active or member_user_ids:
            visible_ids = sorted(set(visible_ids) & set(keep))
        else:
            visible_ids = sorted(set(keep))

    # Metrics — choose the accounts query based on whether the user has
    # narrowed the view to a specific company / brand / member subset.
    #
    #   • UNFILTERED ("All companies"): use a loose OR so the dashboard
    #     reflects EVERY account the team can see — admin-owned brand
    #     assets PLUS team members' own connected accounts. This is the
    #     legacy behaviour and matches what users expect when nothing is
    #     filtered.
    #
    #   • FILTERED: use an "effective owner" rule. An account's effective
    #     owner is the assignee if it's delegated, otherwise the owner.
    #     The account counts only when that effective-owner user_id is
    #     in visible_ids. This prevents the previous leak where an admin
    #     account delegated to a non-visible member still passed via
    #     `user_id IN visible_ids`.
    filter_active = (
        bool(member_user_ids)
        or bool(selected_brand_ids)
        or bool(dna_product_id)
        or any([filter_company, filter_country, filter_state, filter_city, filter_pin_code])
    )
    from sqlalchemy import or_ as _or_, and_ as _and_
    if filter_active:
        accounts_q = db.query(SocialAccount).filter(
            _or_(
                # Delegated → only count when the assignee is visible.
                _and_(
                    SocialAccount.assigned_to_user_id.isnot(None),
                    SocialAccount.assigned_to_user_id.in_(visible_ids),
                ),
                # Self-managed → owner must be visible.
                _and_(
                    SocialAccount.assigned_to_user_id.is_(None),
                    SocialAccount.user_id.in_(visible_ids),
                ),
            )
        )
    else:
        accounts_q = db.query(SocialAccount).filter(
            _or_(
                SocialAccount.user_id.in_(visible_ids),
                SocialAccount.assigned_to_user_id.in_(visible_ids),
            )
        )
    if platform and platform != 'all':
        accounts_q = accounts_q.filter(SocialAccount.platform == platform.strip().lower())
    # Dedupe in TWO passes:
    #
    #   1. By DB primary-key (`a.id`) — guards against the OR-clause
    #      returning the same row twice when user_id == assigned_to_user_id.
    #
    #   2. By the platform's NATIVE account id (`a.account_id`) — guards
    #      against stale duplicate rows in the SocialAccount table itself.
    #      A re-connect can create a fresh row for the same logical
    #      LinkedIn / Facebook page without cleaning up the old one. Both
    #      rows then have different `id`s but identical `account_id`s. Each
    #      got its own `{platform}_{db_id}` entry in
    #      UserAnalyticsSnapshot.platform_breakdown, doubling the
    #      historical follower sum for that account during the window
    #      where both rows existed (e.g., the user's "Z-NINTH 452 → 904
    #      → 452" cliff between Apr 18 and May 2).
    #
    #   We pick the most recently synced row of each duplicate group as
    #   the canonical one — older rows are dropped from `accounts`, so
    #   their stale breakdown entries no longer match `visible_acc_ids`
    #   and stop polluting the chart.
    accounts_raw = accounts_q.all()
    _seen_acc_ids = set()
    _by_native = {}  # (platform, native_account_id) -> SocialAccount
    for a in accounts_raw:
        if a.id in _seen_acc_ids:
            continue
        _seen_acc_ids.add(a.id)
        key = (a.platform, a.account_id)
        existing = _by_native.get(key)
        if existing is None:
            _by_native[key] = a
            continue
        # Keep whichever was synced most recently (or has follower_count > 0).
        a_sync = a.last_analytics_sync
        e_sync = existing.last_analytics_sync
        if a_sync and (not e_sync or a_sync > e_sync):
            _by_native[key] = a
    accounts = list(_by_native.values())
    curr_f = sum(int(acc.follower_count or 0) for acc in accounts)
    
    # Check if a sync is needed or in progress
    has_synced = any(acc.last_analytics_sync for acc in accounts)
    last_sync = max([acc.last_analytics_sync for acc in accounts if acc.last_analytics_sync]) if has_synced else None
    
    # Find a baseline for "prev follower count".
    #
    # Goal: KPI deltas should answer "how much have your followers grown
    # since you joined Pipelyt?" — even when the user has only been on
    # the platform for a few days but the dashboard is showing the 30D
    # window. Otherwise the baseline gets read from a snapshot taken
    # AFTER signup (because there is no snapshot from before signup) and
    # the percentage shows random noise instead of real growth.
    #
    # Decision rule:
    #   - Earliest connect time (across all accounts) = `earliest_connect`.
    #   - If `since` (the requested window cutoff) is BEFORE `earliest_connect`,
    #     the user wasn't on Pipelyt at the cutoff yet → anchor on the
    #     connect-time baseline (`initial_follower_count`).
    #   - Otherwise (user has real history before the window), use the
    #     last snapshot taken before the cutoff as the baseline.
    #   - Fallbacks for legacy data: snapshot before cutoff → initial → earliest.
    #
    # IMPORTANT: ignore snapshots where total_followers == 0 — those are
    # failed syncs (rate-limited / token expired), not real "lost all
    # followers" events. Using them as baseline produces absurd KPIs.
    earliest_connect_dates = [
        a.initial_connected_at for a in accounts if a.initial_connected_at
    ]
    earliest_connect = min(earliest_connect_dates) if earliest_connect_dates else None

    def _virtual_initial_snap():
        """Build a synthetic snapshot from connect-time values so the
        downstream code (which uses .total_followers, .platform_breakdown,
        .snapshot_date) works uniformly."""
        baseline_sum = sum(int(a.initial_follower_count or 0) for a in accounts)
        if baseline_sum <= 0:
            return None

        class _VirtualSnap:
            pass

        vs = _VirtualSnap()
        vs.total_followers = baseline_sum
        vs.platform_breakdown = {
            f"{a.platform}_{a.id}": int(a.initial_follower_count or 0)
            for a in accounts
        }
        vs.snapshot_date = earliest_connect or now
        # Marker so the prev_f computation below knows this snap already
        # includes EVERY account's initial_follower_count and skips the
        # `new_additions_baseline` step (which would double-count every
        # account except the one connected first — see the
        # Info@z-ninth.com 25,610-spike incident).
        vs._is_synthetic_anchor = True
        return vs

    # ALWAYS prefer the connect-time baseline (initial_follower_count).
    # Per product spec: KPIs should always answer "how much have your
    # followers grown SINCE YOU JOINED PIPELYT?" — the time-period toggle
    # then narrows the engagement / reach metrics by that window, while
    # the follower delta remains the lifetime number. This keeps the
    # message consistent ("you've grown +X% since signing up") whether
    # the user is on the 24H, 7D, 30D, or Custom view.
    #
    # Fall back to snapshot-before-cutoff or earliest snapshot ONLY when
    # the account has no initial_follower_count recorded (legacy data).
    user_is_new_to_window = (
        earliest_connect is not None and earliest_connect > since
    )

    prev_snap = _virtual_initial_snap()

    if not prev_snap:
        # No initial baselines recorded → fall back to snapshot before cutoff.
        prev_snap = db.query(UserAnalyticsSnapshot).filter(
            UserAnalyticsSnapshot.user_id == current_user.id,
            UserAnalyticsSnapshot.snapshot_date <= since,
            UserAnalyticsSnapshot.total_followers > 0,
        ).order_by(UserAnalyticsSnapshot.snapshot_date.desc()).first()

    if not prev_snap:
        # Last resort: earliest real snapshot ever recorded.
        prev_snap = db.query(UserAnalyticsSnapshot).filter(
            UserAnalyticsSnapshot.user_id == current_user.id,
            UserAnalyticsSnapshot.total_followers > 0,
        ).order_by(UserAnalyticsSnapshot.snapshot_date.asc()).first()

    if prev_snap:
        prev_f = prev_snap.total_followers
        # Grounded Baseline: if new accounts were added AFTER prev_snap,
        # their 'initial_follower_count' must be added to prev_f to avoid
        # treating their base audience as "new growth" today.
        #
        # IMPORTANT: skip this entirely when prev_snap is the synthetic
        # anchor built from initial_follower_counts. The anchor already
        # sums every account's baseline AND its snapshot_date is
        # earliest_connect, so every account except the very first one
        # would otherwise pass the `> snapshot_date` filter and get its
        # initial_follower_count counted a second time. That bug produced
        # `prev_f = 25,610` for Info@z-ninth.com (real total 12,836,
        # double-counted to 25,610 -> chart line stuck at 25,610 across
        # the window before snapping back to curr_f at the right edge).
        if not getattr(prev_snap, "_is_synthetic_anchor", False):
            new_additions_baseline = sum(
                int(acc.initial_follower_count or 0)
                for acc in accounts
                if acc.initial_connected_at and acc.initial_connected_at > prev_snap.snapshot_date
            )
            prev_f += new_additions_baseline
    else:
        # No snapshot at all — use the connect-time baseline for all accounts.
        # This gives an honest "growth since joining Pipelyt" delta.
        prev_f = sum(int(acc.initial_follower_count or 0) for acc in accounts) or curr_f
    
    # Implicit brand narrowing — when the caller asked to narrow by
    # member_user_ids (i.e. company filter on the trends card), the post
    # query needs to honour that beyond just user_id. In multi-tenant
    # setups the admin clicks publish for everyone, so every PublishedPost
    # row carries user_id == admin_id regardless of which brand it was
    # for; filtering posts by user_id alone leaves Z-Ninth's KPIs equal
    # to NeuzenAI's, which is what the user just spotted on the chart.
    #
    # Build the list of DNA product IDs owned by / assigned to the
    # currently-visible users and treat that as an implicit brand filter
    # whenever an explicit one wasn't supplied.
    implicit_brand_ids: list[str] = []
    if member_user_ids and not selected_brand_ids and not dna_product_id:
        visible_users = db.query(User).filter(User.id.in_(visible_ids)).all()
        _ids: set[str] = set()
        for u in visible_users:
            uids = list(getattr(u, "assigned_dna_product_ids", None) or [])
            if not uids and getattr(u, "assigned_dna_product_id", None):
                uids = [u.assigned_dna_product_id]
            _ids.update(str(x) for x in uids if x)
        implicit_brand_ids = sorted(_ids)

    def get_period_stats(s_date, e_date=None):
        q = db.query(PublishedPostPlatform).join(PublishedPost).filter(
            PublishedPost.user_id.in_(visible_ids),
            PublishedPost.created_at >= s_date,
        )
        if e_date: q = q.filter(PublishedPost.created_at < e_date)
        # Brand narrowing — prefer the multi-id list when present so KPIs
        # line up with the cascading filter on the dashboard. When the
        # caller didn't pass an explicit brand list but DID narrow by
        # member, fall back to the implicit list derived from those
        # members' assignments so the engagement/reach KPIs actually
        # change between Z-Ninth and NeuzenAI views.
        if selected_brand_ids:
            q = q.filter(PublishedPost.dna_product_id.in_(selected_brand_ids))
        elif dna_product_id:
            q = q.filter(PublishedPost.dna_product_id == dna_product_id)
        elif implicit_brand_ids:
            q = q.filter(PublishedPost.dna_product_id.in_(implicit_brand_ids))
        e, r = 0, 0
        for p in q.all():
            m = p.metrics_json or {}
            e += int(m.get("engagement") or 0)
            r += int(m.get("reach") or 0)
        return e, r

    # A-2 fix: custom range now passes `end` as the upper bound so data
    # outside the requested window is excluded. For the built-in ranges
    # (24h / 7d / 30d) `end` falls through to "now" via None.
    # NOTE: `end` is only assigned inside the `time_period == 'custom'`
    # branch at the top of this handler; we mirror that guard here to avoid
    # a NameError on the built-in ranges.
    custom_bounded = (time_period == 'custom' and start_date and end_date)
    curr_end = end if custom_bounded else None  # noqa: F821 — `end` defined in the custom branch
    curr_e, curr_r = get_period_stats(since, curr_end)
    prev_e, prev_r = get_period_stats(prev_since, since)

    def calc(c, p, baseline_mode=None):
        # `baseline_mode`:
        #   None       — plain percentage. Still used by follower KPIs
        #                because the baseline is always large enough.
        #   "noisy"    — return "New" when EITHER condition holds:
        #                  (1) baseline is below a small-data floor
        #                      (prev < 10 for counts, < 0.1 for rates), OR
        #                  (2) growth is explosive (curr > prev * 5, i.e.
        #                      anything above +400%) — at that ratio the
        #                      percentage is sampling noise dressed up as
        #                      a viral spike.
        #                Drops still render as a normal percentage so the
        #                user sees real declines. Used by engagement,
        #                reach, and engagement-rate KPIs.
        if baseline_mode == "noisy":
            small_baseline = 0.1 if isinstance(p, float) else 10
            growing = c > p
            if growing and p < small_baseline:
                return "New"
            if growing and p > 0 and c > p * 5:
                return "New"
        if p == 0:
            return "+0.00%" if c == 0 else "+100%"
        ch = ((c - p) / p) * 100
        return f"{'+' if ch >= 0 else ''}{ch:.2f}%"

    # History — respects the selected time-range so the chart x-axis
    # updates when the admin flips between 24H / 7D / 30D / custom.
    # Previously hardcoded to 30d which made the 24H tab still show
    # 30 days of snapshots, misleading the viewer. `since` was computed
    # at the top of this endpoint from the `range` param and we reuse it
    # here so summary + chart are always on the same window.
    # Team-wide history for admins: always query snapshots for the team owner
    # so every member sees the same consistent history baseline.
    history_owner_id = current_user.team_owner_id or current_user.id
    
    # A-13 Fix: Strictly calculate the history floor based on the selected range.
    # Previously these branches referenced a `range` identifier, which in
    # Python resolves to the builtin — so every comparison was False and the
    # chart x-axis always fell through to `since`. Now uses `time_period`
    # (the actual query-param name).
    if time_period == '24h':
        history_since = now - timedelta(hours=24)
    elif time_period == '7d':
        history_since = now - timedelta(days=7)
    elif time_period == '30d':
        history_since = now - timedelta(days=30)
    elif time_period == '90d':
        history_since = now - timedelta(days=90)
    elif time_period == '1y':
        history_since = now - timedelta(days=365)
    elif time_period == 'all':
        history_since = since
    elif time_period == 'custom' and start_date and end_date:
        history_since = datetime.fromisoformat(start_date)
    else:
        history_since = since

    # For multi-day views, start from the beginning of the day for consistent X-axis.
    if time_period in ('7d', '30d', '90d', '1y', 'all'):
        history_since = history_since.replace(hour=0, minute=0, second=0, microsecond=0)

    history_snaps = db.query(UserAnalyticsSnapshot).filter(
        UserAnalyticsSnapshot.user_id == history_owner_id,
        UserAnalyticsSnapshot.snapshot_date >= history_since,
    ).order_by(UserAnalyticsSnapshot.snapshot_date.asc()).all()

    # ANCHOR THE FOLLOWER LINE AT CONNECT TIME (always, per "growth
    # since signup" semantic). The synthetic anchor row carries the
    # initial_follower_count so the dashed Follower-Growth-% line
    # literally starts from day-1 on Pipelyt. Engagement/reach/etc.
    # weren't measurable until first sync, so they stay at 0 on the
    # anchor row — only the follower line is affected.
    #
    # The anchor is dropped into history_snaps regardless of the
    # selected time window: when the user is on a long window
    # (e.g. Custom > 30 days), they see the whole journey; when
    # they're on a short window the chart x-axis just clips at the
    # window edge but the baseline remains the connect-time value.
    # Skip the synthetic anchor when ANY narrowing filter is active. The
    # anchor sums initial_follower_count across the FILTERED account set,
    # but those connect-time numbers can be much larger than today's
    # filtered total (e.g. an old Z-NINTH LinkedIn page connected at 904
    # followers that has since dropped to 444). The chart would then plot
    # an enormous spike on day one followed by a cliff to the current
    # number — exactly the "904 → 454" drop the user spotted on the
    # Z-NINTH-filtered view. The "growth since signup" semantic is only
    # meaningful for the unfiltered view; under a filter we let the real
    # snapshots speak for themselves.
    if earliest_connect is not None and not filter_active:
        anchor_followers = sum(int(a.initial_follower_count or 0) for a in accounts)
        if anchor_followers > 0:
            class _AnchorSnap:
                pass
            anchor = _AnchorSnap()
            anchor.snapshot_date = earliest_connect
            anchor.total_followers = anchor_followers
            anchor.total_engagement = 0
            anchor.total_reach = 0
            anchor.total_likes = 0
            anchor.total_comments = 0
            anchor.total_shares = 0
            anchor.platform_breakdown = {
                f"{a.platform}_{a.id}": int(a.initial_follower_count or 0)
                for a in accounts
            }
            # Insert ahead of the first real snapshot so the chart line starts
            # from the connect-time baseline.
            history_snaps = [anchor] + list(history_snaps)
    # Build history with two safeguards for the followers line:
    #  1. Snapshots where total_followers == 0 are failed syncs — carry
    #     forward the last good value instead of plotting a cliff to 0.
    #  2. Expose follower_change_pct per day (vs the first good day in
    #     the window) so the frontend can show a % growth marker on hover
    #     — more meaningful than raw counts for brand reporting.
    # Build history with two safeguards for the followers line:
    #  1. Snapshots where total_followers == 0 are failed syncs — carry
    #     forward the last good value instead of plotting a cliff to 0.
    #  2. Expose follower_change_pct per day (vs the first good day in
    #     the window) so the frontend can show a % growth marker on hover
    #     — more meaningful than raw counts for brand reporting.
    # ─────────────────────────────────────────────────────────────────
    # CHART HISTORY — built so engagement / reach / likes / comments /
    # shares lines SUM TO THE TOP KPI CARDS exactly when the same window
    # is selected, and respect the platform / brand / member filters.
    #
    # Previously these lines came from UserAnalyticsSnapshot totals, which
    # are CUMULATIVE running counts across every post ever published.
    # That's why the chart tooltip would show 4,966 reach while the KPI
    # card showed 1,150 for the same 7D window — different sources.
    #
    # New approach: bucket posts by their creation day, sum their
    # metrics_json fields per bucket, and respect every filter the
    # KPI summary uses (visible_ids, platform, brand). Followers stay
    # snapshot-based because they're a STATE, not a flow.
    # ─────────────────────────────────────────────────────────────────

    # 1. Pull the filtered posts in the window — same filter shape as the
    #    KPI summary's get_period_stats(), plus a platform narrowing when
    #    one is selected.
    posts_q = (
        db.query(PublishedPostPlatform, PublishedPost)
          .join(PublishedPost, PublishedPost.id == PublishedPostPlatform.published_post_id)
          .filter(PublishedPost.user_id.in_(visible_ids))
          .filter(PublishedPost.created_at >= history_since)
    )
    if platform and platform != 'all':
        posts_q = posts_q.filter(PublishedPostPlatform.platform == platform.strip().lower())
    if selected_brand_ids:
        posts_q = posts_q.filter(PublishedPost.dna_product_id.in_(selected_brand_ids))
    elif dna_product_id:
        posts_q = posts_q.filter(PublishedPost.dna_product_id == dna_product_id)
    elif implicit_brand_ids:
        # Mirror the implicit narrowing applied in get_period_stats so
        # the chart's engagement/reach lines stay aligned with the KPI
        # ribbon when the user filters by company.
        posts_q = posts_q.filter(PublishedPost.dna_product_id.in_(implicit_brand_ids))

    # Group key: per-day for 7d/30d/custom, per-hour for 24h.
    def _bucket_key(dt):
        if time_period == '24h':
            return dt.replace(minute=0, second=0, microsecond=0)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    # Per-bucket increments: posts whose creation day == bucket key.
    daily_delta = {}
    for ppp, pp in posts_q.all():
        m = ppp.metrics_json or {}
        key = _bucket_key(pp.created_at)
        if key not in daily_delta:
            daily_delta[key] = {"engagement": 0, "reach": 0, "likes": 0, "comments": 0, "shares": 0}
        daily_delta[key]["engagement"] += int(m.get("engagement") or 0)
        daily_delta[key]["reach"]      += int(m.get("reach")      or 0)
        daily_delta[key]["likes"]      += int(m.get("likes")      or 0)
        daily_delta[key]["comments"]   += int(m.get("comments")   or 0)
        daily_delta[key]["shares"]     += int(m.get("shares")     or 0)

    # Build a CUMULATIVE running total for each metric across the window.
    # The chart should trace a smooth growth curve — earlier behaviour
    # zeroed out days where no posts were created (e.g. Apr 26 / Apr 28
    # in the user's account) even though existing posts were still
    # accruing engagement. Cumulative aggregation keeps the line going
    # up monotonically and the LAST bucket's totals match the KPI cards
    # exactly (since they sum every post in the window).
    sorted_delta_keys = sorted(daily_delta.keys())
    running = {"engagement": 0, "reach": 0, "likes": 0, "comments": 0, "shares": 0}
    cumulative_at = {}  # bucket_key -> cumulative metrics state at that key
    for key in sorted_delta_keys:
        d = daily_delta[key]
        for k in running:
            running[k] += d[k]
        cumulative_at[key] = dict(running)
    # The "max so far" lookup helps fill buckets that have no post-creation
    # event but still need to show the running total carried forward.
    def _running_at(key):
        # Find the most recent cumulative_at[k] for k <= key.
        prior = [k for k in sorted_delta_keys if k <= key]
        if not prior:
            return {"engagement": 0, "reach": 0, "likes": 0, "comments": 0, "shares": 0}
        return cumulative_at[prior[-1]]

    # Replace the per-day delta with the cumulative running total so the
    # downstream loop emits the right numbers per bucket.
    daily = {}
    for key in sorted_delta_keys:
        daily[key] = cumulative_at[key]

    # 2. Followers line still comes from snapshots (state-over-time).
    #    Build a lookup of nearest-good follower count per bucket.
    #
    # Platform-aware: when the user filters by a single platform the
    # historical follower line should track ONLY that platform across
    # every date — not show the combined total for old dates and a
    # platform-only number for the most recent one. We extract the
    # per-platform value from snapshot.platform_breakdown
    # (keyed as `{platform}_{accountId}`) and sum any matching keys.
    # Falls back to total_followers when the breakdown doesn't carry an
    # entry for the requested platform (legacy snapshots from before
    # the breakdown column was populated).
    plat_filter = (platform or '').strip().lower()
    is_platform_filter = bool(plat_filter and plat_filter != 'all')

    # Set of account IDs that pass ALL active filters (company, brand,
    # platform, location). The historical follower line should only sum
    # snapshot.platform_breakdown entries whose account_id is in this set
    # — otherwise an admin who owns multiple companies' accounts would
    # see the historical line spike with the COMBINED total and then
    # cliff-drop to the filtered subset on the latest data point.
    visible_acc_ids = {a.id for a in accounts}
    # Build a per-(platform, native account_id) lookup so we can collapse
    # historical breakdown entries that came from stale duplicate
    # SocialAccount rows. A re-connect can leave two DB rows for the
    # same logical platform page; the breakdown stored both, doubling
    # the historical follower sum during the duplicate window. We
    # group breakdown entries by their account.account_id (the native
    # platform-side id, e.g. the LinkedIn org URN) and only count each
    # native account once per snapshot.
    _accid_by_db = {a.id: (a.platform, a.account_id) for a in accounts}
    has_account_filter = (
        is_platform_filter
        or bool(member_user_ids)
        or bool(selected_brand_ids)
        or bool(dna_product_id)
        or any([filter_company, filter_country, filter_state, filter_city, filter_pin_code])
    )
    # Sanity ceiling: cap the historical reading at today's curr_f for
    # the visible accounts. Any historical breakdown that reads higher
    # than today's live total is almost certainly polluted by duplicate
    # SocialAccount rows that have since been deduped — followers don't
    # halve overnight in normal usage. Clamping at exactly curr_f means
    # a polluted window flattens to today's number instead of producing
    # the phantom +100% spike the user just flagged. Legitimate audience
    # losses are rare and small; if they ever produce an expected
    # downturn the clamp will visibly under-report by at most curr_f -
    # actual_historical, which is the conservative side of the trade.
    _curr_visible_total = sum(int(a.follower_count or 0) for a in accounts)
    _ceiling = _curr_visible_total if _curr_visible_total > 0 else 0

    def _follower_count_from_snapshot(snap) -> int:
        breakdown = (snap.platform_breakdown or {})

        # When the breakdown is available, prefer per-account summing —
        # it's the only way to honour a company/brand/platform filter
        # against historical snapshots whose `total_followers` is the
        # admin's COMBINED count across all delegated accounts.
        if breakdown:
            # First pass: group each breakdown entry by its (platform,
            # native account_id) so duplicate db_id rows for the same
            # logical platform page collapse to a single entry. Within a
            # group we keep the MAX value (more conservative than
            # summing — a re-connect's two rows usually report the same
            # follower count, so max == single entry == correct value).
            per_native = {}  # (plat, native_acc_id) -> value
            unmatched_native_keys = []  # for entries whose db_id we don't recognise
            for k, v in breakdown.items():
                try:
                    parts = str(k).rsplit('_', 1)
                    if len(parts) != 2:
                        continue
                    key_plat = parts[0].lower()
                    key_acc  = int(parts[1])
                    val = int(v or 0)
                except Exception:
                    continue
                if is_platform_filter and key_plat != plat_filter:
                    continue
                if has_account_filter and key_acc not in visible_acc_ids:
                    continue
                native = _accid_by_db.get(key_acc)
                if native is None:
                    # Account no longer in DB but has a breakdown entry.
                    # Track it under a synthetic key so it still
                    # contributes once (max), without merging with live
                    # accounts on the same platform.
                    native = (key_plat, f"_legacy_{key_acc}")
                prev = per_native.get(native, 0)
                if val > prev:
                    per_native[native] = val

            plat_sum = sum(per_native.values())
            any_match = bool(per_native)

            if any_match:
                # Apply the sanity ceiling so a polluted snapshot can't
                # drag the chart's followers line above 1.5× today's
                # filtered total.
                if _ceiling > 0 and plat_sum > _ceiling:
                    plat_sum = _ceiling
                return plat_sum
            # Fall through if breakdown had no matching keys at all (legacy).
            plat_sum = 0
            any_match = False
            for k, v in breakdown.items():
                try:
                    parts = str(k).rsplit('_', 1)
                    if len(parts) != 2:
                        continue
                    key_plat = parts[0].lower()
                    key_acc  = int(parts[1])
                except Exception:
                    continue
                if is_platform_filter and key_plat != plat_filter:
                    continue
                if has_account_filter and key_acc not in visible_acc_ids:
                    continue
                plat_sum += int(v or 0)
                any_match = True
            if any_match:
                return plat_sum
            # Breakdown didn't include any of the visible accounts. This
            # is a snapshot taken before those accounts existed, OR a
            # filter so narrow that nothing matches. Either way, return 0
            # so the chart skips this date instead of plotting the
            # admin's pre-filter combined total — which would render as
            # the misleading historical-spike-then-cliff the user
            # complained about.
            if has_account_filter:
                return 0
            # No filter active and breakdown empty for visible accounts?
            # Fall through to total_followers below.

        # No breakdown column on this snapshot at all (very early data).
        if is_platform_filter:
            # Scale by current platform share as a last-resort estimate.
            curr_total = sum(int(a.follower_count or 0) for a in accounts) or 1
            curr_plat  = sum(int(a.follower_count or 0) for a in accounts if a.platform == plat_filter) or 0
            if curr_total > 0 and curr_plat > 0:
                share = curr_plat / curr_total
                return int(round(int(snap.total_followers or 0) * share))
            return 0
        if has_account_filter:
            # We can't attribute legacy total_followers to specific
            # accounts, so suppress the data point rather than show a
            # misleading combined number under a narrow filter.
            return 0
        return int(snap.total_followers or 0)

    follower_at = {}  # bucket_key -> follower count on or before that bucket
    # Recompute baseline_followers for the platform-filtered case so the
    # right-axis "Follower Growth %" anchors at the right value.
    if is_platform_filter:
        baseline_followers = sum(
            int(a.initial_follower_count or 0) for a in accounts if a.platform == plat_filter
        ) or sum(
            int(a.follower_count or 0) for a in accounts if a.platform == plat_filter
        )
    else:
        baseline_followers = int(prev_f or 0)
    last_good_followers = baseline_followers
    for s in history_snaps:
        f = _follower_count_from_snapshot(s)
        if f <= 0:
            continue
        # Suspicious cliff-drop (sync failure) — keep last good value.
        if last_good_followers > 100 and f < (last_good_followers * 0.1):
            continue
        last_good_followers = f
        follower_at[_bucket_key(s.snapshot_date)] = f

    # 3. Compose the unified history. For every bucket where EITHER
    #    posts or follower snapshots exist, emit one row. Forward-fill
    #    follower count AND the cumulative metrics across days that
    #    didn't have a post created or a sync that day — that prevents
    #    the chart from dropping to 0 on quiet days even though the
    #    running totals are higher.
    all_keys = sorted(set(list(daily.keys()) + list(follower_at.keys())))
    history = []
    running_followers = baseline_followers
    for key in all_keys:
        if key in follower_at:
            running_followers = follower_at[key]
        # Use the running cumulative as of this bucket key; if there's a
        # post-creation entry for this key it's already merged into the
        # running total, so this naturally yields the same value as the
        # max-so-far lookup either way.
        bucket = _running_at(key)

        if baseline_followers > 0:
            pct = round(((running_followers - baseline_followers) / baseline_followers) * 100, 2)
        else:
            pct = 0.0

        history.append({
            "date":                 key.isoformat(),
            "engagement":           bucket["engagement"],
            "reach":                bucket["reach"],
            "likes":                bucket["likes"],
            "comments":             bucket["comments"],
            "shares":               bucket["shares"],
            "followers":            running_followers,
            "follower_change_pct":  pct,
        })

    # A-13 Fix: Final, extremely strict history slicing.
    # We use explicit cutoffs to ensure the X-axis is perfect.
    history_cutoff = now - timedelta(days=7) # default
    if time_period == '24h':
        history_cutoff = now - timedelta(hours=24)
    elif time_period == '30d':
        history_cutoff = now - timedelta(days=30)
    elif time_period == '90d':
        history_cutoff = now - timedelta(days=90)
    elif time_period == '1y':
        history_cutoff = now - timedelta(days=365)
    elif time_period == 'all':
        history_cutoff = datetime(2020, 1, 1)
    elif time_period == 'custom' and start_date:
        history_cutoff = datetime.fromisoformat(start_date)
    
    # Filter the list by the strict cutoff
    history = [h for h in history if datetime.fromisoformat(h["date"]) >= history_cutoff]

    # Final visual safeguard: if history is empty (brand new account),
    # provide two points to prevent the "No data" chart state.
    if not history:
        # Inline follower % delta — matches the summary-card logic:
        # (current - previous) / previous * 100. Previously called an
        # undefined `calc_num()` helper which crashed the whole
        # /analytics/summary endpoint for any user without snapshots
        # (CORS errors in the browser were a symptom, not the cause).
        delta_pct = round(((curr_f - prev_f) / prev_f) * 100, 2) if prev_f else 0.0
        history = [
            {"date": (now - timedelta(days=1)).isoformat(),
             "engagement": 0, "reach": 0, "likes": 0, "comments": 0, "shares": 0, "followers": prev_f, "follower_change_pct": 0},
            {"date": now.isoformat(),
             "engagement": curr_e, "reach": curr_r, "likes": 0, "comments": 0, "shares": 0, "followers": curr_f, "follower_change_pct": delta_pct},
        ]

    # Time-period bucketing — ensures the chart x-axis always spans the
    # requested window at the right granularity, regardless of how many
    # snapshots exist:
    #   24h  → 24 hourly buckets (00:00…23:00 relative to "now")
    #   7d   → 7 daily buckets (last 7 dates)
    #   30d  → 30 daily buckets (last 30 dates)
    #   custom → daily buckets covering start_date → end_date
    # Without this, an account with a single daily snapshot would render
    # "05:30" stamped four times across the 24H chart because all ticks
    # map to the single snapshot's timestamp.
    def _bucket_step():
        if time_period == '24h':
            return timedelta(hours=1), 24
        if time_period == '30d':
            return timedelta(days=1), 30
        if time_period == '90d':
            # 90 daily buckets is too dense for a chart axis — collapse to
            # ~30 weekly buckets (3-day step) so labels stay readable.
            return timedelta(days=3), 30
        if time_period == '1y':
            # 1-year view → 12 monthly-ish buckets (~30-day step).
            return timedelta(days=30), 12
        if time_period == 'all':
            # All-time view → 24 buckets covering whatever range the
            # account has data for, with a minimum of 30 days each.
            span_days = max(30, (now - datetime(2020, 1, 1)).days)
            step_days = max(1, span_days // 24)
            return timedelta(days=step_days), 24
        if time_period == 'custom' and start_date and end_date:
            span = datetime.fromisoformat(end_date) - datetime.fromisoformat(start_date)
            days = max(1, span.days + 1)
            # Under a day → hourly, over a day → daily.
            if span.total_seconds() < 60 * 60 * 24:
                return timedelta(hours=1), max(2, int(span.total_seconds() // 3600))
            return timedelta(days=1), days
        # default 7d
        return timedelta(days=1), 7

    step, bucket_count = _bucket_step()
    # Align buckets to the end of the window so the rightmost bucket is
    # always "now" — this way the latest data always appears on the right
    # edge of the chart no matter when the admin opens the page.
    if step == timedelta(hours=1):
        # Round DOWN to the current hour so labels fall on clean H:00 marks.
        anchor = now.replace(minute=0, second=0, microsecond=0)
    else:
        anchor = now.replace(hour=0, minute=0, second=0, microsecond=0)

    bucket_starts = [anchor - step * (bucket_count - 1 - i) for i in range(bucket_count)]

    def _pick_row_for(bucket_end):
        # Most recent history row at or before this bucket's end time.
        # Iterate in reverse since history is chronologically ascending.
        for row in reversed(history):
            try:
                row_dt = datetime.fromisoformat(row["date"])
            except Exception:
                continue
            if row_dt <= bucket_end:
                return row
        return None

    # Build bucket rows with a "carry both ways" strategy so the chart
    # renders as a smooth flat line (or realistic trend if multi-snapshot)
    # instead of a cliff. With only ONE snapshot in the 24H window, the
    # old code emitted zeros for buckets before that snapshot and the
    # real values after → visible vertical cliff on the chart.
    #
    # Strategy:
    #   1. First pass — emit rows with nulls for metrics where no snapshot
    #      is known yet, so the first real row "anchors" the line.
    #   2. Second pass — backfill any leading nulls with the FIRST real
    #      value seen (so followers line is flat at that value from the
    #      window start rather than starting at 0).
    # For `followers` specifically we also fall back to `baseline_followers`
    # (the pre-window prev value) so the line starts at something meaningful
    # even if the first snapshot is mid-window.
    first_real = None
    for row in history:
        if (row.get("followers") or 0) > 0:
            first_real = row
            break

    bucketed = []
    last_row = None
    for b_start in bucket_starts:
        b_end = b_start + step
        row = _pick_row_for(b_end)
        if row is None:
            if last_row:
                # After a snapshot — carry forward the last known values so
                # the right edge of the chart stays pinned to the most
                # recent measurement.
                row = {**last_row}
            else:
                # BEFORE any snapshot — we don't actually know what
                # engagement/reach/likes/comments/shares were at that moment.
                # Emit `None` for those so recharts draws the line from
                # the first real snapshot onward (connectNulls handles the
                # gap cleanly). Followers default to the baseline so the
                # percentage line starts at 0% and rises to current %.
                row = {
                    "engagement": None, "reach": None, "likes": None,
                    "comments": None, "shares": None,
                    "followers": baseline_followers,
                    "follower_change_pct": 0.0,
                }
        else:
            last_row = row
        bucketed.append({**row, "date": b_start.isoformat()})
    history = bucketed

    # Platforms Detail - Keyed by Platform+ID to support multiple accounts per platform
    platforms = {}
    platform_split = []

    aggregated_platforms = {}
    # Per-account `prev_f_acc` resolution — priority order, first match wins:
    #   (a) prev_snap.platform_breakdown has an entry for this exact unique_key
    #   (b) account was connected AFTER prev_snap → use initial_follower_count
    #       (the snapshot can't know about an account that didn't exist yet)
    #   (c) proportional share of snapshot's unaccounted-for remainder
    #       (only legacy snapshots that lack breakdown entirely)
    #   (d) initial_follower_count, else current follower_count (→ 0% change)
    #
    # (b) is the fix for the "+3000% growth" bug: new accounts were being
    # spread thin across the OLD snapshot's small total and showing massive
    # fake growth. They should use their own connect-time baseline instead.
    prev_snap_date = prev_snap.snapshot_date if prev_snap else None
    prev_total_for_prop = int(prev_snap.total_followers or 0) if prev_snap else 0
    # Subtract out accounts that have a breakdown entry AND accounts that
    # were connected after the snapshot (they shouldn't share in the remainder).
    known_prev_sum = 0
    known_curr_sum = 0
    if prev_snap:
        for a in accounts:
            k = f"{a.platform}_{a.id}"
            has_entry = (prev_snap.platform_breakdown or {}).get(k) is not None
            connected_after = (
                a.initial_connected_at is not None
                and prev_snap_date is not None
                and a.initial_connected_at > prev_snap_date
            )
            if has_entry or connected_after:
                known_prev_sum += int((prev_snap.platform_breakdown or {}).get(k) or 0)
                known_curr_sum += int(a.follower_count or 0)
    remainder_prev = max(0, prev_total_for_prop - known_prev_sum)
    curr_total_for_prop = max(1, sum(int(a.follower_count or 0) for a in accounts))
    remainder_curr = max(1, curr_total_for_prop - known_curr_sum)

    # Sum of per-account prev_f; used to rebuild summary prev_f so KPI matches
    # the platform split perfectly.
    sum_prev_f = 0

    for acc in accounts:
        # Fetch platform posts ONCE and compute engagement + reach from the same rows.
        # IMPORTANT: scope to the SELECTED time window AND honour the brand
        # filter — previously this query was unfiltered (lifetime totals)
        # which caused per-platform engagement/reach to exceed the All-
        # platforms KPI when the user picked a 30d window. The All-platforms
        # KPI uses get_period_stats(since, curr_end) with the same brand
        # filtering, so we mirror the exact same shape here. Also computes
        # the previous-period totals so engagement_change / reach_change
        # can be a real % (not the hardcoded "+0.00%" / "+100%" fallback).
        def _acc_metrics(start, end):
            qq = (db.query(PublishedPostPlatform)
                    .join(PublishedPost,
                          PublishedPost.id == PublishedPostPlatform.published_post_id)
                    .filter(PublishedPostPlatform.account_id == acc.account_id)
                    .filter(PublishedPost.user_id.in_(visible_ids))
                    .filter(PublishedPost.created_at >= start))
            if end is not None:
                qq = qq.filter(PublishedPost.created_at < end)
            if selected_brand_ids:
                qq = qq.filter(PublishedPost.dna_product_id.in_(selected_brand_ids))
            elif dna_product_id:
                qq = qq.filter(PublishedPost.dna_product_id == dna_product_id)
            elif implicit_brand_ids:
                qq = qq.filter(PublishedPost.dna_product_id.in_(implicit_brand_ids))
            e_, r_ = 0, 0
            for pp in qq.all():
                m = pp.metrics_json or {}
                e_ += int(m.get("engagement") or 0)
                r_ += int(m.get("reach") or 0)
            return e_, r_

        plat_eng, plat_reach = _acc_metrics(since, curr_end)
        prev_eng_acc, prev_reach_acc = _acc_metrics(prev_since, since)
        # LIFETIME engagement / reach for this account — NOT scoped to
        # the selected time window. The Platform Performance heatmap
        # pairs each account's lifetime follower count with its
        # engagement; if engagement stayed window-scoped while
        # followers were lifetime, an account whose posts predate the
        # 7d window showed "0.0%" performance share even though it has
        # plenty of lifetime engagement. Lifetime-vs-lifetime keeps the
        # heatmap's two halves comparable.
        lifetime_eng, lifetime_reach = _acc_metrics(datetime(2000, 1, 1), None)

        # Unique ID entry
        unique_key = f"{acc.platform}_{acc.id}"
        prev_f_acc = (prev_snap.platform_breakdown or {}).get(unique_key) if prev_snap else None

        if prev_f_acc is None:
            # (b) Account connected AFTER the snapshot — snapshot can't know
            # about it; use its own initial_follower_count baseline.
            connected_after_snap = (
                prev_snap_date is not None
                and acc.initial_connected_at is not None
                and acc.initial_connected_at > prev_snap_date
            )
            if connected_after_snap:
                prev_f_acc = int(acc.initial_follower_count or 0) or int(acc.follower_count or 0)
            elif prev_snap and remainder_prev > 0 and remainder_curr > 0:
                # (c) Proportional share for legacy-snapshot accounts
                share = int(acc.follower_count or 0) / remainder_curr
                prev_f_acc = int(round(remainder_prev * share))
            else:
                # (d) Best effort: connect-time baseline, else neutral 0% change
                prev_f_acc = int(acc.initial_follower_count or 0) or int(acc.follower_count or 0)

        sum_prev_f += int(prev_f_acc or 0)

        platforms[unique_key] = {
            "name": f"{acc.platform.capitalize()} ({acc.name})",
            "platform_type": acc.platform,
            "followers": int(acc.follower_count or 0),
            "follower_change": calc(int(acc.follower_count or 0), prev_f_acc),
            "engagement": plat_eng,
            "reach": plat_reach,
            # Lifetime totals — used by the Platform Performance heatmap
            # so its Performance Share half compares apples-to-apples
            # with the lifetime `followers` count on the other half.
            "lifetime_engagement": lifetime_eng,
            "lifetime_reach": lifetime_reach,
        }
        
        # Aggregated entry (e.g. 'linkedin')
        p_slug = acc.platform.lower()
        if p_slug not in aggregated_platforms:
            aggregated_platforms[p_slug] = {"total_followers": 0, "total_engagement": 0, "total_reach": 0, "prev_f": 0, "prev_e": 0, "prev_r": 0}
        
        aggregated_platforms[p_slug]["total_followers"] += int(acc.follower_count or 0)
        aggregated_platforms[p_slug]["total_engagement"] += plat_eng
        aggregated_platforms[p_slug]["total_reach"] += plat_reach
        aggregated_platforms[p_slug]["prev_f"] += prev_f_acc
        aggregated_platforms[p_slug]["prev_e"] += prev_eng_acc
        aggregated_platforms[p_slug]["prev_r"] += prev_reach_acc
        
        platform_split.append({
            "name": f"{acc.platform.capitalize()} ({acc.name})",
            "value": int(acc.follower_count or 0),
            "change": calc(int(acc.follower_count or 0), prev_f_acc)
        })

    # Finalize aggregate changes
    for p_slug in aggregated_platforms:
        p_data = aggregated_platforms[p_slug]
        p_data["follower_change"] = calc(p_data["total_followers"], p_data["prev_f"])
        
        # Calculate engagement rates for platforms
        curr_p_rate = (p_data["total_engagement"] / p_data["total_followers"] * 100) if p_data["total_followers"] > 0 else 0
        prev_p_rate = (p_data["prev_e"] / p_data["prev_f"] * 100) if p_data["prev_f"] > 0 else 0
        p_data["engagement_rate"] = f"{curr_p_rate:.1f}%"
        p_data["engagement_rate_change"] = calc(curr_p_rate, prev_p_rate, baseline_mode="noisy")
        
        p_data["engagement_change"] = calc(p_data["total_engagement"], p_data["prev_e"], baseline_mode="noisy")
        p_data["reach_change"] = calc(p_data["total_reach"], p_data["prev_r"], baseline_mode="noisy")

    # Override prev_f with the sum of per-account prev_f values so the
    # summary KPI matches the platform-split dropdown exactly. The original
    # prev_snap.total_followers-based prev_f could disagree with the sum
    # when accounts fell back to initial_follower_count / current.
    if accounts:
        prev_f = sum_prev_f

    # Recompute follower-trajectory values on every history bucket using
    # the FINAL prev_f (post sum_prev_f override) and curr_f (live account
    # sum). Three things must line up exactly with the summary card:
    #   1. Rightmost bucket's `followers` == curr_f (the big number)
    #   2. Rightmost bucket's `follower_change_pct` == the "+X.XX%" delta
    #   3. Leftmost (leading) buckets start at 0% so the line rises from
    #      zero → current delta instead of sitting at a random pre-baseline.
    recalc_baseline = int(prev_f or 0)

    def _pct(current, base):
        if base <= 0 or not current:
            return 0.0
        return round(((current - base) / base) * 100, 2)

    for h in history:
        # "Leading" buckets are the ones we emitted before any snapshot;
        # they have engagement=None as a marker. Pin their followers back
        # to the final baseline so pct stays a clean 0% — otherwise the
        # pre-override baseline they stored leaks a tiny non-zero pct.
        if h.get("engagement") is None:
            h["followers"] = recalc_baseline
            h["follower_change_pct"] = 0.0
        else:
            h["follower_change_pct"] = _pct(h.get("followers") or 0, recalc_baseline)

    # The last bucket always represents "right now". Hard-pin it to
    # curr_f + the authoritative summary delta so the chart's final
    # point can never drift away from the Total Followers tile — that
    # mismatch ("+0.33% on card vs +0.10% on chart") is exactly what
    # the user just flagged.
    if history:
        history[-1]["followers"] = int(curr_f or 0)
        history[-1]["follower_change_pct"] = _pct(int(curr_f or 0), recalc_baseline)

    # TEMP DIAG — dumps the KPI math so we can compare chart vs card.
    # Remove once the chart and summary KPI reliably agree in prod.
    try:
        logger.info(
            "[chart-kpi-diag] user=%s time_period=%s curr_f=%s prev_f=%s "
            "recalc_baseline=%s summary_calc=%s last_bucket_pct=%s",
            current_user.id, time_period, curr_f, prev_f,
            recalc_baseline, calc(curr_f, prev_f),
            history[-1]["follower_change_pct"] if history else None,
        )
    except Exception:
        pass

    curr_rate = (curr_e / curr_f * 100) if curr_f > 0 else 0
    prev_rate = (prev_e / prev_f * 100) if prev_f > 0 else 0

    return {
        "summary": {
            "total_followers": curr_f, "follower_change": calc(curr_f, prev_f),
            "total_engagement": curr_e, "engagement_change": calc(curr_e, prev_e, baseline_mode="noisy"),
            "total_reach": curr_r, "reach_change": calc(curr_r, prev_r, baseline_mode="noisy"),
            "engagement_rate": f"{curr_rate:.1f}%",
            "engagement_rate_change": calc(curr_rate, prev_rate, baseline_mode="noisy"),
            "platform_split": platform_split,
            "is_syncing": not has_synced and len(accounts) > 0,
            "last_sync": last_sync.isoformat() + "Z" if last_sync else None
        },
        "platforms": platforms,
        "aggregated_platforms": aggregated_platforms,
        "history": history,
        # A-9: top_posts was computed (full joinedload + sort + slice) but the
        # frontend never renders it. Removed the expensive query to cut DB work
        # + response payload size. If the frontend adds a "Top Posts" card
        # later, re-add this block.
        "top_posts": [],
    }

@router.get("/analytics/posts")
async def get_detailed_posts_analytics(
    platform: str = None, time_period: str = '7d', start_date: str = None, end_date: str = None,
    dna_product_id: str = None,
    dna_product_ids: str = None,   # multi-brand csv — see /analytics/summary
    member_user_ids: str = None,  # admin-only: csv of team-member ids
    # Profile filters — mirror /analytics/summary so the posts list stays in
    # sync when the admin picks e.g. "country=IN" on the dashboard.
    filter_company: str = None,
    filter_country: str = None,
    filter_state: str = None,
    filter_city: str = None,
    filter_pin_code: str = None,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    # 1. Feature Check
    check_feature(current_user, "analytics")

    now = datetime.utcnow()
    # Robust since handling — previously used a `range` param which shadowed
    # the Python builtin and always made every branch False, so the posts
    # endpoint silently served a 7-day window regardless of the frontend
    # selection. Renamed to `time_period` to match the frontend query param.
    days = 7
    if time_period == '30d': days = 30
    elif time_period == '24h': days = 1  # A-6 fix: was 2 (intentional buffer but inconsistent with summary)
    elif time_period == 'custom' and start_date and end_date:
        # Calculate days from start_date manually
        try:
            d_start = datetime.fromisoformat(start_date.split('T')[0])
            days = (now - d_start).days + 1
        except Exception:
            days = 30

    # Team scope + optional admin profile/member-ids filters.
    visible_ids = get_team_scope_user_ids(db, current_user)
    if is_team_member(current_user):
        from services.team_service import get_member_assigned_dna_ids
        allowed = set(get_member_assigned_dna_ids(current_user))
        if dna_product_id and dna_product_id not in allowed:
            dna_product_id = None
        member_user_ids = None
    else:
        # Profile filters run first — narrows to members matching company /
        # country / state / city / pin code. Any combination ANDs.
        profile_filters_active = any([
            filter_company, filter_country, filter_state,
            filter_city, filter_pin_code,
        ])
        if profile_filters_active:
            from sqlalchemy import func as _sqlfunc
            q_ids = db.query(User.id).filter(User.team_owner_id == current_user.id)
            if filter_company:
                q_ids = q_ids.filter(_sqlfunc.lower(User.member_company_name) == filter_company.strip().lower())
            if filter_country:
                q_ids = q_ids.filter(User.country == filter_country.strip())
            if filter_state:
                q_ids = q_ids.filter(User.state == filter_state.strip())
            if filter_city:
                q_ids = q_ids.filter(_sqlfunc.lower(User.city) == filter_city.strip().lower())
            if filter_pin_code:
                q_ids = q_ids.filter(User.pin_code == filter_pin_code.strip())
            visible_ids = sorted({int(r[0]) for r in q_ids.all()})

        if member_user_ids:
            try:
                requested = [int(x) for x in member_user_ids.split(",") if x.strip()]
            except Exception:
                requested = []
            if requested:
                # Same fix as in /analytics/summary: accept the admin's own
                # id alongside team-member ids. Without this, an admin
                # filtering to their own company was silently dropped from
                # member_user_ids and the response widened to the full team.
                team_ids = {
                    int(r[0]) for r in db.query(User.id).filter(
                        User.team_owner_id == current_user.id,
                        User.id.in_(requested),
                    ).all()
                }
                valid_ids = set(team_ids)
                if int(current_user.id) in requested:
                    valid_ids.add(int(current_user.id))
                if profile_filters_active:
                    valid_ids = valid_ids & set(visible_ids)
                if valid_ids:
                    visible_ids = sorted(valid_ids)

        # Brand filter — same priority logic as /analytics/summary.
        selected_brand_ids: list[str] = []
        if dna_product_ids:
            selected_brand_ids = [x.strip() for x in dna_product_ids.split(",") if x.strip()]
        elif dna_product_id:
            selected_brand_ids = [dna_product_id]

        if selected_brand_ids:
            sel_set = set(selected_brand_ids)
            brand_members = db.query(User).filter(
                User.team_owner_id == current_user.id,
            ).all()
            keep = [int(current_user.id)]
            for m in brand_members:
                mids = set(
                    (m.assigned_dna_product_ids or [])
                    or ([m.assigned_dna_product_id] if m.assigned_dna_product_id else [])
                )
                if mids & sel_set:
                    keep.append(int(m.id))
            if profile_filters_active or member_user_ids:
                visible_ids = sorted(set(visible_ids) & set(keep))
            else:
                visible_ids = sorted(set(keep))

    since = now - timedelta(days=days)
    q = db.query(PublishedPost).options(joinedload(PublishedPost.platform_posts)).filter(
        PublishedPost.user_id.in_(visible_ids),
        PublishedPost.created_at >= since,
    )
    # Multi-brand post filter takes priority over the single-id legacy param.
    post_brand_ids: list[str] = []
    if dna_product_ids:
        post_brand_ids = [x.strip() for x in dna_product_ids.split(",") if x.strip()]
    elif dna_product_id:
        post_brand_ids = [dna_product_id]
    if post_brand_ids:
        q = q.filter(PublishedPost.dna_product_id.in_(post_brand_ids))
    posts = q.all()

    # Bulk-load account rows so each row can show which account (personal
    # profile vs page) it was published to — the Campaign Performance
    # table used to show the raw platform icon only, which made two
    # posts from the same LinkedIn app indistinguishable.
    _pairs = {(pp.platform, pp.account_id)
              for post in posts for pp in post.platform_posts if pp.account_id}
    _acc_map = {}
    if _pairs:
        _rows = db.query(SocialAccount).filter(
            SocialAccount.platform.in_({pl for pl, _ in _pairs}),
            SocialAccount.account_id.in_({aid for _, aid in _pairs}),
        ).all()
        for a in _rows:
            _acc_map[(a.platform, a.account_id)] = {
                "name": a.name or "",
                "type": a.type or "",
                "is_personal": (a.platform == "linkedin"
                                and (a.account_id or "").startswith("urn:li:person:")),
            }

    results = []
    for post in posts:
        for p in post.platform_posts:
            if platform and p.platform != platform and platform != 'all': continue
            m = p.metrics_json or {}
            info = _acc_map.get((p.platform, p.account_id)) or {}
            results.append({
                "id": post.id, "native_id": p.native_post_id, "content": post.content, "image_url": post.image_url,
                # media_type lets the Campaign Performance popup render the
                # right element ('image' → <img>, 'video' → <video>,
                # 'document' → file chip). Without it the popup falls back
                # to URL-extension detection only, which breaks for files
                # that don't carry a recognizable extension in their S3 URL.
                "media_type": post.media_type,
                "platform": p.platform, "publish_date": post.created_at.isoformat() + "Z",
                # Account-identifying fields — Campaign Performance renders
                # these as a small chip after the platform icon so the user
                # can tell a personal-profile post apart from a page post.
                "account_id": p.account_id,
                "account_name": info.get("name") or "",
                "account_type": info.get("type") or "",
                "is_personal": bool(info.get("is_personal")),
                "likes": int(m.get("likes") or 0), "comments": int(m.get("comments") or 0), "shares": int(m.get("shares") or 0),
                "reach": int(m.get("reach") or 0), "engagement": int(m.get("engagement") or 0),
                "sentiment": m.get("sentiment")
            })
    return sorted(results, key=lambda x: x["publish_date"] or "", reverse=True)

@router.get("/analytics/post/{platform}/{native_id}/sentiment")
async def get_post_sentiment_analysis(
    platform: str, native_id: str,
    refresh: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetch comments for a specific post and perform AI sentiment analysis.

    Serves cached `metrics_json.sentiment` (populated by /analytics/sync) when
    it is <24h old. Pass ?refresh=true to force a re-analysis.
    """
    # 1. Feature Check
    check_feature(current_user, "analytics")

    # 2. Cache-aware serve — the /analytics/sync loop populates sentiment
    #    for every post with comments, so most modal opens can avoid the
    #    external comment fetch + LLM call entirely.
    ppp_cached = db.query(PublishedPostPlatform).filter(
        PublishedPostPlatform.native_post_id == native_id,
        PublishedPostPlatform.platform == platform
    ).first()
    if ppp_cached and not refresh:
        cached = (ppp_cached.metrics_json or {}).get("sentiment") or {}
        if cached.get("analyzed_at"):
            try:
                last = datetime.fromisoformat(cached["analyzed_at"].rstrip("Z"))
                # HEAL CACHE: We now require granular analyzed_comments to consider the cache "valid".
                # If these are missing, we force a re-analysis that populates the full breakdown.
                if (datetime.utcnow() - last) < timedelta(hours=24) and cached.get("analyzed_comments"):
                    return {"status": "success", "data": cached, "cached": True}
            except Exception:
                pass

    # 3. Find Social Account
    account = db.query(SocialAccount).filter(
        SocialAccount.user_id == current_user.id,
        SocialAccount.platform == platform
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail=f"No connected {platform} account found")

    # 4. Fetch Comments
    comments = []
    error_msg = None

    try:
        if platform == 'facebook':
            comments, error_msg = fetch_facebook_comments(account.token, native_id)
        elif platform == 'instagram':
            comments, error_msg = fetch_instagram_comments(account.token, native_id)
        elif platform == 'linkedin':
            # Note: LinkedIn ORG posts require org_urn. 
            # We assume account.account_id stores the org_urn for org accounts.
            comments, error_msg = fetch_linkedin_org_comments(account.token, native_id, account.account_id)
        elif platform == 'twitter':
            comments, error_msg = fetch_twitter_comments(
                TWITTER_API_KEY, TWITTER_API_SECRET, 
                account.token, account.token_secret, native_id
            )
        else:
            raise HTTPException(status_code=400, detail=f"Sentiment analysis not supported for {platform}")
    except Exception as e:
        logger.error(f"Sentiment fetch error for {platform}/{native_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch comments: {str(e)}")

    if error_msg:
        logger.warning(f"Sentiment fetch warning for {platform}/{native_id}: {error_msg}")
        raise HTTPException(status_code=400, detail=error_msg)

    # 4. Enforce 1-comment minimum (reduced from 5 to ensure reliable triggering)
    if len(comments) < 1:
        return {
            "status": "insufficient_data",
            "message": f"Analysis requires at least 1 comment (found {len(comments)})",
            "count": len(comments)
        }

    # 5. Get Post Context (for business-aware analysis)
    post_context = None
    try:
        ppp = db.query(PublishedPostPlatform).filter(
            PublishedPostPlatform.native_post_id == native_id,
            PublishedPostPlatform.platform == platform
        ).first()
        if ppp and ppp.published_post:
            post_context = ppp.published_post.content
    except Exception as e:
        logger.warning(f"Failed to fetch post context for sentiment: {e}")

    # 6. AI Sentiment Analysis
    try:
        analysis = analyze_comments_sentiment(comments, post_context=post_context)
        if "error" in analysis:
            raise HTTPException(status_code=500, detail=analysis["error"])

        # Persist to metrics_json so the next modal open skips the LLM call.
        if ppp_cached is not None:
            m = dict(ppp_cached.metrics_json or {})
            m["sentiment"] = {
                "overall_score": analysis.get("overall_score"),
                "overall_sentiment": analysis.get("overall_sentiment"),
                "overall_summary": analysis.get("overall_summary"),
                "top_insight": analysis.get("top_insight"),
                "sentiment_counts": analysis.get("sentiment_counts"),
                "analyzed_comments": analysis.get("analyzed_comments"),
                "analyzed_at": datetime.utcnow().isoformat() + "Z",
                "comment_count": len(comments),
            }
            ppp_cached.metrics_json = m
            db.commit()

        return {
            "status": "success",
            "data": analysis
        }
    except Exception as e:
        logger.error(f"AI Sentiment analysis failed: {e}")
        raise HTTPException(status_code=500, detail="AI Analysis failed")


# ---------------------------------------------------------------------
# Comments — per-post fetch + reply (LinkedIn Org / Facebook / Instagram)
# Twitter/X and LinkedIn member profiles intentionally omitted: the app
# does not hold r_member_social and the Twitter free-tier recent-search
# endpoint would blow the rate limit on every modal open.
# ---------------------------------------------------------------------

class CommentReplyBody(BaseModel):
    message: str
    parent_comment_id: str | None = None  # for IG/FB threaded replies


class AccountBaselineBody(BaseModel):
    """Set the baseline follower count for a connected account.

    Use case: seed users, or anyone whose initial_follower_count was
    captured at first-sync (current value at the time) instead of the
    actual connect-time number. Setting this corrects the %-change KPIs
    without waiting for a 7-day-old snapshot to accumulate.
    """
    follower_count: int


def _resolve_platform_post(
    db: Session, user: User, post_id: int, platform: str,
    instance_id: Optional[int] = None,
    native_id: Optional[str] = None,
    account_id: Optional[str] = None,
):
    """Resolve a specific PublishedPostPlatform row.

    Historically only `instance_id` was accepted, but when a single post is
    published to multiple accounts on the same platform (e.g. LinkedIn
    personal + LinkedIn page) and the caller doesn't have the instance_id
    handy, `native_id` and/or `account_id` disambiguate.
    """
    visible_ids = get_team_scope_user_ids(db, user)

    query = (
        db.query(PublishedPostPlatform)
        .join(PublishedPost)
        .filter(
            PublishedPost.user_id.in_(visible_ids),
            PublishedPost.id == post_id,
            PublishedPostPlatform.platform == platform,
        )
    )

    if instance_id:
        query = query.filter(PublishedPostPlatform.id == instance_id)
    if native_id:
        query = query.filter(PublishedPostPlatform.native_post_id == native_id)
    if account_id:
        query = query.filter(PublishedPostPlatform.account_id == account_id)

    pp = query.first()
    
    if not pp:
        raise HTTPException(status_code=404, detail="Post not found for this platform/instance")
    # The SocialAccount that authored this post lives on the original publisher
    # (admin or whichever team member). Look it up by platform + account_id
    # without re-filtering by the current user so admin can act on team-member posts.
    account = (
        db.query(SocialAccount)
        .filter(
            SocialAccount.user_id.in_(visible_ids),
            SocialAccount.platform == platform,
            SocialAccount.account_id == pp.account_id,
        )
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Connected account not found")
    return pp, account


@router.post("/analytics/posts/{post_id}/refresh")
async def refresh_single_post_metrics(
    post_id: int,
    background_tasks: BackgroundTasks,
    platform: Optional[str] = Query(None),
    native_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the current DB metrics for this post AND kick off a
    background sync of the accounts that published it. Powers the
    Analytics preview modal's auto-refresh + Refresh Stats button.

    Design — split the work so the modal isn't blocked:

      1. Immediately aggregate whatever's already in
         `PublishedPostPlatform.metrics_json` and return it — the stat
         tiles paint instantly with the last-known numbers.
      2. Enqueue the account-wide sync as a FastAPI BackgroundTask so
         `sync_account_analytics` (which bulk-fetches EVERY post the
         account has ever published — Meta / Twitter / LinkedIn) runs
         AFTER the response has been sent. Next Refresh click (or next
         modal open) sees the freshly-synced numbers.

    Prior implementation ran the sync synchronously inside the request
    and the modal spun for 5+ minutes while sync_account_analytics
    walked 254 Meta posts / 170 Twitter posts / all LinkedIn history.
    Response `sync_status: 'queued'` lets the frontend optionally
    display a "syncing in background" hint.
    """
    check_feature(current_user, "analytics")

    # Tenant scope — same visibility rules the list endpoint uses.
    visible_ids = get_team_scope_user_ids(db, current_user)
    post = (
        db.query(PublishedPost)
        .filter(PublishedPost.id == post_id, PublishedPost.user_id.in_(visible_ids))
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Scope to a specific (platform, native_id) when the frontend
    # provides it. The Campaign Performance table lists ONE ROW per
    # (post × platform) — clicking "Instagram Post" opens the modal
    # for the IG-specific version, so the numbers must be IG-only.
    # Without this scoping the endpoint SUMMED metrics across all
    # platforms a post was cross-posted to, so a post live on IG + FB +
    # X + LinkedIn showed 4× the like count. Falls back to the
    # aggregate (all platforms) when the frontend didn't supply
    # platform info — matches the analytics summary card behaviour.
    q = db.query(PublishedPostPlatform).filter(
        PublishedPostPlatform.published_post_id == post_id
    )
    if platform:
        q = q.filter(PublishedPostPlatform.platform == platform.lower())
    if native_id:
        q = q.filter(PublishedPostPlatform.native_post_id == str(native_id))
    rows = q.all()

    # Aggregate current DB numbers for immediate response.
    totals = {"likes": 0, "comments": 0, "shares": 0, "reach": 0}
    per_platform = {}
    last_synced_at = None
    for pr in rows:
        m = pr.metrics_json or {}
        # Build the per-platform snapshot from the RAW metrics_json for
        # this row (must NOT read from `totals` after we start
        # accumulating into it — that would mix values across rows).
        per_platform[pr.platform] = {k: int(m.get(k) or 0) for k in totals}
        for k in totals:
            v = m.get(k) or 0
            try: totals[k] += int(v)
            except Exception: pass
        if pr.last_synced_at and (last_synced_at is None or pr.last_synced_at > last_synced_at):
            last_synced_at = pr.last_synced_at

    engagement_denom = totals["reach"] or 1
    engagement = round((totals["likes"] + totals["comments"] + totals["shares"]) / engagement_denom * 100, 2)

    # Enqueue the account-wide sync to run AFTER we send the response.
    # sync_account_analytics needs its OWN DB session — the request's
    # session closes when the response is sent — so open one inside.
    from sqlalchemy import or_ as _or_
    platform_account_pairs = list({(pr.platform, pr.account_id) for pr in rows if pr.account_id})

    if platform_account_pairs:
        def _bg_sync(user_ids: list[int], account_pairs: list[tuple[str, str]]):
            from core.database import SessionLocal
            _db = SessionLocal()
            try:
                accounts = _db.query(SocialAccount).filter(
                    _or_(
                        SocialAccount.user_id.in_(user_ids),
                        SocialAccount.assigned_to_user_id.in_(user_ids),
                    )
                ).all()
                pair_set = set(account_pairs)
                synced = 0
                for acc in accounts:
                    if (acc.platform, acc.account_id) in pair_set:
                        try:
                            sync_account_analytics(_db, acc, force=True)
                            synced += 1
                        except Exception as e:
                            logger.warning(
                                f"[bg refresh_single_post_metrics] sync failed "
                                f"account_id={acc.id} platform={acc.platform}: {e}"
                            )
                logger.info(
                    f"[bg refresh_single_post_metrics] post_id={post_id} synced={synced}"
                )
            finally:
                _db.close()

        background_tasks.add_task(_bg_sync, list(visible_ids), platform_account_pairs)

    return {
        "post_id": post_id,
        "metrics": {**totals, "engagement": engagement},
        "per_platform": per_platform,
        # Surfacing this lets the frontend show a "last synced Xm ago"
        # hint if we ever want to; harmless to include either way.
        "last_synced_at": last_synced_at.isoformat() + "Z" if last_synced_at else None,
        # 'queued' → background sync fired; the numbers above reflect
        # the LAST completed sync, not the just-triggered one. Next
        # Refresh click (or next modal open ~30-60s later) sees fresh
        # data.
        "sync_status": "queued" if platform_account_pairs else "no_accounts",
    }


@router.get("/analytics/posts/{post_id}/comments")
async def get_post_comments(
    post_id: int,
    platform: str,
    instance_id: Optional[int] = Query(None),
    # native_id + account_id let the caller disambiguate when a single
    # PublishedPost has multiple platform_posts (e.g. LinkedIn personal +
    # LinkedIn page). Without them, _resolve_platform_post picks the first
    # row it finds and may return the wrong account's comment state.
    native_id: Optional[str] = Query(None),
    account_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_feature(current_user, "analytics")
    platform = (platform or "").lower()
    if platform not in {"linkedin", "facebook", "instagram", "twitter", "youtube", "tiktok"}:
        return {"platform": platform, "comments": [], "supported": False,
                "reason": "Comment fetch not supported for this platform yet."}

    pp, account = _resolve_platform_post(
        db, current_user, post_id, platform,
        instance_id=instance_id, native_id=native_id, account_id=account_id,
    )

    comments, err = [], None
    if platform == "linkedin":
        # Personal LinkedIn profiles: LinkedIn's API does not expose
        # personal-post comment text to third-party apps (r_member_social_feed
        # is a closed program). Return a clear, unambiguous message the
        # frontend can render — do NOT set `supported=False`, which would
        # otherwise be mistaken for a per-platform gap. This is a LinkedIn
        # restriction, not a Pipelyt gap.
        if (account.account_id or "").startswith("urn:li:person:"):
            return {
                "platform": platform,
                "comments": [],
                "error": "LinkedIn does not support fetching personal-post comments via API. You can view and reply to them directly on LinkedIn.",
                "supported": True,
                "account_name": account.name,
                "post_native_id": pp.native_post_id,
            }
        comments, err = fetch_linkedin_org_comments(account.token, pp.native_post_id, account.account_id)
    elif platform == "facebook":
        post_id_fmt = pp.native_post_id
        if "_" not in str(post_id_fmt):
            post_id_fmt = f"{account.account_id}_{post_id_fmt}"
        comments, err = fetch_facebook_comments(account.token, post_id_fmt)
    elif platform == "instagram":
        comments, err = fetch_instagram_comments(account.token, pp.native_post_id)
    elif platform == "twitter":
        comments, err = fetch_twitter_comments(
            TWITTER_API_KEY, TWITTER_API_SECRET,
            account.token, account.token_secret, pp.native_post_id,
        )
    elif platform == "youtube":
        # `native_post_id` for YouTube is the video_id we stamped at publish
        # time. The youtube_comments helper refreshes the access token
        # itself (other adapters use the long-lived token directly).
        from services.youtube_comments import fetch_comments as fetch_youtube_comments
        comments, err = fetch_youtube_comments(account, db, pp.native_post_id)
    elif platform == "tiktok":
        # `native_post_id` for TikTok is the video id from publish-status's
        # `publicaly_available_post_id`. Same access-token-refresh contract
        # as YouTube — the helper handles it.
        from services.tiktok_comments import fetch_comments as fetch_tiktok_comments
        comments, err = fetch_tiktok_comments(account, db, pp.native_post_id)

    # If a platform succeeded (no error) but returned zero comments AND the
    # analytics sync's cached badge count says there SHOULD be some, surface
    # a per-platform hint so the frontend can explain the mismatch instead
    # of silently rendering "No comments yet". The badge is looked up on the
    # frontend — here we just tag the response with a candidate reason.
    if not comments and not err:
        try:
            badge_count = int((pp.metrics_json or {}).get("comments") or 0)
        except Exception:
            badge_count = 0
        if badge_count > 0:
            if platform == "twitter":
                err = (
                    "X reports {} reply{} on this post but X's search index "
                    "doesn't return it. Common causes: the replier is a "
                    "low-follower / newly-created account (X excludes those "
                    "from search), the reply is a quote-tweet counted in "
                    "the metric but not indexed, the reply came from a "
                    "private account, or X's index hasn't caught up yet "
                    "(can take hours). The reply is visible on x.com but "
                    "not retrievable via API."
                ).format(badge_count, "" if badge_count == 1 else "ies")
            elif platform == "youtube":
                err = (
                    "YouTube reports {} comment{} on this video but the fetch "
                    "returned nothing. The video may have been deleted, made "
                    "private, or comments disabled — reconnect YouTube if the "
                    "issue persists."
                ).format(badge_count, "" if badge_count == 1 else "s")
            elif platform == "linkedin":
                err = (
                    "LinkedIn reports {} comment{} on this post but the fetch "
                    "came back empty. Reconnect LinkedIn — the stored token "
                    "may be missing the r_organization_social scope."
                ).format(badge_count, "" if badge_count == 1 else "s")
            elif platform == "instagram":
                err = (
                    "Instagram reports {} comment{} on this post but the "
                    "fetch came back empty. Reconnect Instagram — the stored "
                    "token may be missing instagram_manage_comments."
                ).format(badge_count, "" if badge_count == 1 else "s")
            elif platform == "facebook":
                err = (
                    "Facebook reports {} comment{} on this post but the fetch "
                    "came back empty. Reconnect Facebook — the Page token may "
                    "be missing pages_read_engagement."
                ).format(badge_count, "" if badge_count == 1 else "s")

    return {
        "platform": platform,
        "account_name": account.name,
        "post_native_id": pp.native_post_id,
        "comments": comments,
        "error": err,
        "supported": True,
    }


@router.post("/analytics/posts/{post_id}/comments/reply")
async def reply_post_comment(
    post_id: int,
    platform: str,
    body: CommentReplyBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_feature(current_user, "analytics")
    platform = (platform or "").lower()
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Reply message cannot be empty")
    if platform not in {"linkedin", "facebook", "instagram", "twitter", "youtube", "tiktok"}:
        raise HTTPException(status_code=400, detail="Reply not supported for this platform")

    pp, account = _resolve_platform_post(db, current_user, post_id, platform)

    new_id, err = None, None
    if platform == "linkedin":
        if not (account.account_id or "").startswith("urn:li:organization:"):
            raise HTTPException(status_code=400, detail="Reply requires a LinkedIn organization account")
        new_id, err = reply_linkedin_org_comment(
            account.token, pp.native_post_id, account.account_id, text,
            parent_comment_urn=body.parent_comment_id or None,
        )
    elif platform == "facebook":
        target = body.parent_comment_id
        if not target:
            raise HTTPException(status_code=400, detail="parent_comment_id required for Facebook replies")
        new_id, err = reply_facebook_comment(account.token, target, text)
    elif platform == "instagram":
        target = body.parent_comment_id
        if not target:
            raise HTTPException(status_code=400, detail="parent_comment_id required for Instagram replies")
        new_id, err = reply_instagram_comment(account.token, target, text)
    elif platform == "twitter":
        # Reply is either to the root tweet or a specific reply tweet
        target = body.parent_comment_id or pp.native_post_id
        new_id, err = reply_twitter_comment(
            TWITTER_API_KEY, TWITTER_API_SECRET,
            account.token, account.token_secret, target, text,
        )
    elif platform == "youtube":
        # YouTube only allows replying to an existing comment — there's no
        # such thing as "reply to the video itself". So a parent_comment_id
        # is mandatory; the UI passes the comment id the user clicked Reply
        # under.
        target = body.parent_comment_id
        if not target:
            raise HTTPException(
                status_code=400,
                detail="parent_comment_id required for YouTube replies (YouTube does not allow top-level posts via API)",
            )
        from services.youtube_comments import post_reply as post_youtube_reply
        new_id, err = post_youtube_reply(account, db, target, text)
    elif platform == "tiktok":
        # Same as YouTube — TikTok's API has no concept of "top-level
        # comment created by the channel owner". parent_comment_id is
        # mandatory.
        target = body.parent_comment_id
        if not target:
            raise HTTPException(
                status_code=400,
                detail="parent_comment_id required for TikTok replies (TikTok API only supports replies, not new top-level comments)",
            )
        from services.tiktok_comments import post_reply as post_tiktok_reply
        new_id, err = post_tiktok_reply(account, db, target, text)

    if err:
        raise HTTPException(status_code=502, detail=err)
    return {"ok": True, "id": new_id, "platform": platform}


@router.get("/analytics/accounts")
async def list_accounts_with_baselines(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return each connected account + its current vs baseline follower count.
    Admin: self + all team members (owned or assigned). Member: own + admin-assigned."""
    from sqlalchemy import or_ as _or_
    visible_ids = get_team_scope_user_ids(db, current_user)
    accounts = db.query(SocialAccount).filter(
        _or_(
            SocialAccount.user_id.in_(visible_ids),
            SocialAccount.assigned_to_user_id.in_(visible_ids),
        )
    ).all()
    return [
        {
            "id": a.id,
            "platform": a.platform,
            "name": a.name,
            "account_id": a.account_id,
            "owner_user_id": a.user_id,
            "follower_count": int(a.follower_count or 0),
            "initial_follower_count": int(a.initial_follower_count or 0),
            "initial_connected_at": a.initial_connected_at.isoformat() + "Z" if a.initial_connected_at else None,
        }
        for a in accounts
    ]


@router.put("/analytics/accounts/{account_db_id}/baseline")
async def set_account_baseline(
    account_db_id: int,
    body: AccountBaselineBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Override initial_follower_count for one account owned by the user or
    one of their team members (admin only for member accounts)."""
    if body.follower_count < 0:
        raise HTTPException(status_code=400, detail="follower_count must be non-negative")
    visible_ids = get_team_scope_user_ids(db, current_user)
    acc = (
        db.query(SocialAccount)
        .filter(SocialAccount.id == account_db_id, SocialAccount.user_id.in_(visible_ids))
        .first()
    )
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    acc.initial_follower_count = int(body.follower_count)
    if not acc.initial_connected_at:
        acc.initial_connected_at = datetime.utcnow()
    db.commit()
    return {
        "ok": True,
        "id": acc.id,
        "platform": acc.platform,
        "name": acc.name,
        "initial_follower_count": acc.initial_follower_count,
    }
