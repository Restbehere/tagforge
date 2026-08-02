"""Rate-limited async client for Danbooru and Danbooru-compatible boorus.

Implements the four fetch modes from the plan plus a trending-delta helper.
All calls observe a configurable requests-per-second cap, send a mandatory
User-Agent, and use cursor-based pagination (``b<id>``) where the endpoint
supports it for efficient deep pagination.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import urlencode, urlparse

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .. import settings


logger = logging.getLogger(__name__)


class UpstreamTimeout(httpx.HTTPError):
    """Danbooru cancelled the query (Postgres statement timeout).

    Deterministic for a given query shape + account tier — retrying the same
    request is pointless; the query has to be narrowed instead.
    """


class BooruClientError(httpx.HTTPError):
    """A deterministic 4xx (bad key, anon tag cap, page past 1000).

    Raised instead of letting ``raise_for_status`` fire: its message embeds
    the full request URL, and auth travels in the query string, so the
    credentials ended up in tracebacks and log files. Also excluded from
    retries — re-sending a request the server has already refused just
    replays the credentials three more times.
    """


# Tags that are "free" metatags and don't count toward the 2-paid-tag anon cap.
# Source: Danbooru help:cheatsheet, verified 2026-05.
FREE_METATAGS: frozenset[str] = frozenset(
    {
        "rating",
        "date",
        "age",
        "id",
        "limit",
        "score",
        "downvotes",
        "favcount",
        "width",
        "height",
        "ratio",
        "mpixels",
        "filesize",
        "filetype",
        "duration",
        "md5",
        "pixiv_id",
        "parent",
        "child",
        "status",
        "is",
        "tagcount",
    }
)


SITE_HOSTS: dict[str, str] = {
    "danbooru": "https://danbooru.donmai.us",
    "aibooru": "https://aibooru.online",
    "safebooru-donmai": "https://safebooru.donmai.us",
}
# Keep settings.ORIGIN_BOORU in sync with these keys — a site missing there
# disappears from every booru-origin filter in the UI.


def _is_metatag(token: str) -> bool:
    if ":" not in token:
        return False
    key, _, _ = token.partition(":")
    return key.lower() in FREE_METATAGS or key.lower() == "order"


def classify_tag_budget(tags_str: str) -> dict[str, Any]:
    """Return ``{paid, free, anon_ok, gold_ok, message}`` for a tag query.

    Anonymous users may use up to 2 paid tags. ``order:`` counts as one paid
    tag (it is NOT in the free-metatag list). Gold accounts get 6 paid tags.
    """
    tokens = [t for t in tags_str.replace("+", " ").split() if t]
    free = sum(1 for t in tokens if t.lower().startswith(tuple(f + ":" for f in FREE_METATAGS)))
    paid = len(tokens) - free
    anon_ok = paid <= 2
    gold_ok = paid <= 6
    msg = (
        "OK for anonymous use." if anon_ok
        else "Requires at least a Gold account (more than 2 paid tags)."
        if gold_ok
        else "Requires Platinum (more than 6 paid tags)."
    )
    return {"paid": paid, "free": free, "anon_ok": anon_ok, "gold_ok": gold_ok, "message": msg}


@dataclass
class Post:
    """A booru post stripped to the fields we care about."""

    id: int
    rating: Optional[str] = None
    score: Optional[int] = None
    fav_count: Optional[int] = None
    up_score: Optional[int] = None
    created_at: Optional[str] = None
    tag_string_general: str = ""
    tag_string_artist: str = ""
    tag_string_character: str = ""
    tag_string_copyright: str = ""
    tag_string_meta: str = ""
    file_ext: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    source: Optional[str] = None

    @property
    def all_general_tags(self) -> list[str]:
        return [t for t in self.tag_string_general.split() if t]

    @property
    def all_artist_tags(self) -> list[str]:
        return [t for t in self.tag_string_artist.split() if t]

    @property
    def all_character_tags(self) -> list[str]:
        return [t for t in self.tag_string_character.split() if t]

    @property
    def all_copyright_tags(self) -> list[str]:
        return [t for t in self.tag_string_copyright.split() if t]

    @property
    def all_meta_tags(self) -> list[str]:
        return [t for t in self.tag_string_meta.split() if t]


@dataclass
class DanbooruClient:
    site: str = "danbooru"
    login: Optional[str] = None
    api_key: Optional[str] = None
    user_agent: str = settings.DEFAULT_USER_AGENT
    req_per_second: float = settings.DEFAULT_REQ_PER_SECOND
    _last_req: float = field(default=0.0, init=False)
    _client: Optional[httpx.AsyncClient] = field(default=None, init=False)

    @property
    def base_url(self) -> str:
        # Never fall back to using `site` as a literal URL: it arrives from
        # the API/CLI and would let a fetch be pointed at an arbitrary host
        # with the user's booru credentials attached.
        try:
            return SITE_HOSTS[self.site]
        except KeyError:
            raise ValueError(
                f"unknown booru site {self.site!r}; expected one of {sorted(SITE_HOSTS)}"
            ) from None

    @staticmethod
    def _pick_proxy() -> Optional[str]:
        """The configured booru proxy, if its port is actually listening.

        For networks where boorus are unreachable directly, fetches can be
        routed through a local proxy (settings.BOORU_PROXY, e.g. Cloudflare
        WARP in proxy mode). If the proxy is down, fall back to a direct
        connection rather than failing on the proxy hop.
        """
        proxy = settings.BOORU_PROXY
        if not proxy:
            return None
        parsed = urlparse(proxy)
        host, port = parsed.hostname or "127.0.0.1", parsed.port or 1080
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return proxy
        except OSError:
            logger.warning(
                "booru proxy %s is not reachable — falling back to a direct connection",
                proxy,
            )
            return None

    async def __aenter__(self) -> "DanbooruClient":
        proxy = self._pick_proxy()
        if proxy:
            logger.info("booru fetches routed via proxy %s", proxy)
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
            follow_redirects=True,
            proxy=proxy,
        )
        return self

    async def __aexit__(self, *_exc):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        gap = 1.0 / max(self.req_per_second, 0.5)
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_req
        if elapsed < gap:
            await asyncio.sleep(gap - elapsed)
        self._last_req = asyncio.get_event_loop().time()

    async def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        if self._client is None:
            raise RuntimeError("DanbooruClient must be used as an async context manager")

        # Auth via query params (the API documents login=, api_key=).
        query = dict(params)
        if self.login and self.api_key:
            query["login"] = self.login
            query["api_key"] = self.api_key

        url = f"{self.base_url}{endpoint}"
        await self._throttle()

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential_jitter(initial=1, max=30),
            retry=(
                retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError))
                & retry_if_not_exception_type((UpstreamTimeout, BooruClientError))
            ),
            reraise=True,
        ):
            with attempt:
                logger.debug(
                    "GET %s %s",
                    url,
                    {**query, "api_key": "***"} if "api_key" in query else query,
                )
                resp = await self._client.get(url, params=query)
                if resp.status_code == 429:
                    raise httpx.HTTPError("rate-limited (429)")
                if resp.status_code >= 500:
                    # Danbooru 500s carry a JSON body; QueryCanceled means the
                    # statement timeout fired — deterministic, don't retry.
                    err = msg = ""
                    try:
                        detail = resp.json()
                        err = detail.get("error", "")
                        msg = detail.get("message", "")
                    except Exception:
                        pass
                    if err == "ActiveRecord::QueryCanceled":
                        raise UpstreamTimeout(
                            f"upstream {resp.status_code} — {msg or 'query timed out'}"
                        )
                    raise httpx.HTTPError(
                        f"upstream {resp.status_code}" + (f" — {msg}" if msg else "")
                    )
                if 400 <= resp.status_code < 500:
                    # Never call raise_for_status here: its message embeds the
                    # full URL, and login/api_key ride in the query string.
                    detail = ""
                    try:
                        detail = (resp.json() or {}).get("message", "")
                    except Exception:
                        pass
                    raise BooruClientError(
                        f"{endpoint} returned {resp.status_code}"
                        + (f" — {detail}" if detail else "")
                    )
                resp.raise_for_status()
                return resp.json()
        return None  # unreachable

    @staticmethod
    def _to_post(raw: dict) -> Post:
        return Post(
            id=int(raw["id"]),
            rating=raw.get("rating"),
            score=raw.get("score"),
            fav_count=raw.get("fav_count"),
            up_score=raw.get("up_score"),
            created_at=raw.get("created_at"),
            tag_string_general=raw.get("tag_string_general", ""),
            tag_string_artist=raw.get("tag_string_artist", ""),
            tag_string_character=raw.get("tag_string_character", ""),
            tag_string_copyright=raw.get("tag_string_copyright", ""),
            tag_string_meta=raw.get("tag_string_meta", ""),
            file_ext=raw.get("file_ext"),
            image_width=raw.get("image_width"),
            image_height=raw.get("image_height"),
            source=raw.get("source"),
        )

    @staticmethod
    def _only_fields() -> str:
        return ",".join(
            [
                "id",
                "rating",
                "score",
                "fav_count",
                "up_score",
                "created_at",
                "tag_string_general",
                "tag_string_artist",
                "tag_string_character",
                "tag_string_copyright",
                "tag_string_meta",
                "file_ext",
                "image_width",
                "image_height",
                "source",
            ]
        )

    async def popular(self, date: str, scale: str = "day", limit: int = 200) -> list[Post]:
        data = await self._get(
            "/explore/posts/popular.json",
            {"date": date, "scale": scale, "limit": min(limit, 200), "only": self._only_fields()},
        )
        return [self._to_post(p) for p in (data or [])]

    async def posts(
        self,
        tags: str,
        *,
        limit: int = 200,
        pages: int = 1,
        cursor: Optional[str] = None,
        numbered_pages: bool = False,
    ) -> list[Post]:
        """Generic ``posts.json`` query with pagination.

        ``numbered_pages=True`` uses ``page=N`` offsets instead of the default
        ``page=b{id}`` cursor.  Cursor pagination only works with Danbooru's
        default ``order:id`` sort; any other ``order:`` tag (score, rank, …)
        requires numbered pages otherwise Danbooru returns HTTP 500 past the
        first page.

        Expensive predicates (e.g. ``score:>N`` on a million-post tag) hit
        Danbooru's statement timeout at any page. Cursor-mode queries fall
        back to id-windowed fetching, which bounds each scan; numbered-page
        callers use order-dependent sorts that windows would scramble, so
        they re-raise.
        """
        out: list[Post] = []
        last_id: Optional[int] = None
        # An order: metatag can arrive inside the user's own tag string, not
        # just from a caller that knows to pass numbered_pages. Cursor mode
        # would then 500 past page 1 — or, via the windowed fallback, return
        # confidently wrong results, since windows scramble a non-id sort.
        if not numbered_pages and any(
            t.lower().startswith("order:") for t in tags.split()
        ):
            numbered_pages = True
        # Danbooru caps a page at 200; comparing the short-page check against
        # the raw `limit` would end pagination after page 1 for limit > 200.
        page_size = min(limit, 200)
        try:
            for page_idx in range(max(1, pages)):
                params: dict[str, Any] = {
                    "tags": tags,
                    "limit": page_size,
                    "only": self._only_fields(),
                }
                if numbered_pages:
                    params["page"] = page_idx + 1
                elif page_idx == 0 and cursor:
                    params["page"] = cursor
                elif page_idx > 0 and last_id is not None:
                    params["page"] = f"b{last_id}"

                batch = await self._get("/posts.json", params)
                if not batch:
                    break
                posts = [self._to_post(p) for p in batch]
                out.extend(posts)
                last_id = posts[-1].id if posts else None
                if len(posts) < page_size:
                    break
            return out
        except UpstreamTimeout:
            if numbered_pages:
                raise
            logger.warning(
                "query %r hit Danbooru's statement timeout — "
                "switching to id-windowed fetching",
                tags,
            )
        return await self._posts_windowed(
            tags, limit=limit, target=min(limit, 200) * max(1, pages), out=out
        )

    async def _posts_windowed(
        self, tags: str, *, limit: int, target: int, out: list[Post]
    ) -> list[Post]:
        """Walk the id space downward in bounded windows until ``target``.

        ``id:lo..hi`` caps how many rows a query can touch, which keeps each
        request under the statement timeout no matter how expensive the
        predicate is. The window adapts: halve on a timeout, grow through
        sparse stretches. 500k ids is the empirically safe start for the
        worst case measured (huge tag + score filter, anonymous tier).
        """
        if out:
            hi = out[-1].id - 1
        else:
            probe = await self._get("/posts.json", {"limit": 1, "only": "id"})
            if not probe:
                return out
            hi = int(probe[0]["id"])

        window, min_w, max_w = 500_000, 50_000, 4_000_000
        while hi > 0 and len(out) < target:
            lo = max(0, hi - window)
            try:
                cur: Optional[int] = None
                while len(out) < target:
                    params: dict[str, Any] = {
                        "tags": f"{tags} id:{lo}..{hi}",
                        "limit": min(limit, 200),
                        "only": self._only_fields(),
                    }
                    if cur is not None:
                        params["page"] = f"b{cur}"
                    batch = await self._get("/posts.json", params)
                    if not batch:
                        break
                    posts = [self._to_post(p) for p in batch]
                    out.extend(posts)
                    cur = posts[-1].id
                    if len(batch) < min(limit, 200):
                        break
            except UpstreamTimeout:
                if cur is not None:
                    # Keep the progress the window made before timing out.
                    hi = cur - 1
                if window <= min_w:
                    raise  # even a minimal window times out — give up honestly
                window = max(min_w, window // 2)
                logger.info("windowed fetch: timeout, narrowing to %d ids", window)
                continue
            hi = lo - 1
            # Ramp back up so sparse tags cross the id space quickly.
            window = min(max_w, int(window * 1.5))
        return out[:target]

    async def top_by_rank(
        self,
        *,
        date_min: Optional[str] = None,
        date_max: Optional[str] = None,
        rating: Optional[str] = None,
        score_min: Optional[int] = None,
        limit: int = 200,
        pages: int = 1,
    ) -> list[Post]:
        parts = ["order:rank"]
        if rating:
            parts.append(f"rating:{rating}")
        if date_min and date_max and date_min == date_max:
            # Single-day fetch (used by date-range backfill iteration).
            parts.append(f"date:{date_min}")
        else:
            if date_min:
                parts.append(f"date:>={date_min}")
            if date_max:
                parts.append(f"date:<={date_max}")
        if score_min is not None:
            parts.append(f"score:>{score_min}")
        return await self.posts(" ".join(parts), limit=limit, pages=pages, numbered_pages=True)

    async def top_by_score(
        self,
        *,
        date_min: Optional[str] = None,
        date_max: Optional[str] = None,
        rating: Optional[str] = None,
        score_min: Optional[int] = None,
        limit: int = 200,
        pages: int = 1,
    ) -> list[Post]:
        parts = ["order:score"]
        if rating:
            parts.append(f"rating:{rating}")
        if date_min and date_max:
            parts.append(f"date:{date_min}..{date_max}")
        elif date_min:
            parts.append(f"date:>={date_min}")
        if score_min is not None:
            parts.append(f"score:>{score_min}")
        return await self.posts(" ".join(parts), limit=limit, pages=pages, numbered_pages=True)

    async def tag_search(
        self,
        *,
        tags: Iterable[str],
        rating: Optional[str] = None,
        order: Optional[str] = None,
        date_min: Optional[str] = None,
        date_max: Optional[str] = None,
        score_min: Optional[int] = None,
        limit: int = 200,
        pages: int = 1,
    ) -> list[Post]:
        parts: list[str] = []
        if order:
            parts.append(f"order:{order}")
        parts.extend(tags)
        if rating:
            parts.append(f"rating:{rating}")
        if date_min and date_max:
            parts.append(f"date:{date_min}..{date_max}")
        elif date_min:
            parts.append(f"date:>={date_min}")
        if score_min is not None:
            parts.append(f"score:>{score_min}")
        return await self.posts(" ".join(parts), limit=limit, pages=pages)

    async def trending_delta(
        self,
        *,
        recent_days: int = 7,
        baseline_days: int = 30,
        rating: Optional[str] = None,
        pages: int = 2,
    ) -> dict[str, Any]:
        """Fetch two date windows and compute per-tag frequency deltas."""
        from datetime import datetime, timedelta

        today = datetime.utcnow().date()
        r_start = (today - timedelta(days=recent_days)).isoformat()
        b_start = (today - timedelta(days=baseline_days)).isoformat()

        recent = await self.posts(
            " ".join(
                ["order:score", f"date:>={r_start}"] + ([f"rating:{rating}"] if rating else [])
            ),
            limit=200,
            pages=pages,
        )
        baseline = await self.posts(
            " ".join(
                ["order:score", f"date:{b_start}..{r_start}"]
                + ([f"rating:{rating}"] if rating else [])
            ),
            limit=200,
            pages=pages,
        )

        def count(posts: list[Post]) -> dict[str, int]:
            d: dict[str, int] = {}
            for p in posts:
                for t in p.all_general_tags:
                    d[t] = d.get(t, 0) + 1
            return d

        rc = count(recent)
        bc = count(baseline)
        all_tags = set(rc) | set(bc)
        items = []
        for t in all_tags:
            r = rc.get(t, 0)
            b = bc.get(t, 0)
            items.append({"name": t, "recent": r, "baseline": b, "ratio": (r + 1) / (b + 1)})
        items.sort(key=lambda x: x["ratio"], reverse=True)
        return {
            "recent_window_start": r_start,
            "baseline_window_start": b_start,
            "recent_posts": len(recent),
            "baseline_posts": len(baseline),
            "items": items,
        }
