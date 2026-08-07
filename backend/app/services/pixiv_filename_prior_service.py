"""Canonical Pixiv filename/path prior parsing shared by ingestion workflows.

The runtime denominator follows the accepted SCV2 import grammar: a bounded
7-12 digit work id with an optional ``_pN``/``-pN`` suffix. Bare ids mean page
zero. Historical Phase 4.4-P0 reporting pins its narrower legacy grammar in its
own runner so changing this durable boundary cannot rewrite old evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


PARSER_VERSION = "scv2_pixiv_filename_prior_v2"
PIXIV_PRIOR_PATTERN = r"(?<!\d)(?P<pixiv_work_id>\d{7,12})(?:[_-]p(?P<page_index>\d+))?(?!\d)"
PIXIV_PRIOR_RE = re.compile(PIXIV_PRIOR_PATTERN, re.IGNORECASE)


@dataclass(frozen=True, order=True)
class PixivFilenamePrior:
    work_id: str
    page_index: int
    source_field: str
    token: str


def extract_pixiv_filename_prior_from_text(value: str | None) -> list[dict[str, object]]:
    """Return canonical accepted-SCV2 work/page priors for one text value."""

    text_value = str(value or "")
    found: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()
    for match in PIXIV_PRIOR_RE.finditer(text_value):
        work_id = str(int(match.group("pixiv_work_id")))
        if work_id == "0" or len(work_id) > 12:
            continue
        page_index = int(match.group("page_index") or 0)
        key = (work_id, page_index)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "pixiv_work_id": work_id,
                "page_index": page_index,
                "token": match.group(0),
                "span": [match.start(), match.end()],
            }
        )
    return found


def parse_approved_fields(fields: Iterable[tuple[str, str | None]]) -> tuple[PixivFilenamePrior, ...]:
    """Parse approved filename/path evidence while preserving field origin."""

    found: set[PixivFilenamePrior] = set()
    for source_field, value in fields:
        for match in extract_pixiv_filename_prior_from_text(value):
            found.add(
                PixivFilenamePrior(
                    work_id=str(match["pixiv_work_id"]),
                    page_index=int(match["page_index"]),
                    source_field=str(source_field),
                    token=str(match["token"]),
                )
            )
    return tuple(sorted(found))


def distinct_work_pages(priors: Iterable[PixivFilenamePrior]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted({(item.work_id, item.page_index) for item in priors}))
