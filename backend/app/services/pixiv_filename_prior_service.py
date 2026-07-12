"""Canonical Pixiv filename/path prior parsing shared by ingestion workflows.

This module promotes the accepted Phase 4.4-P0 parser into a reusable runtime
boundary.  The parser version and token grammar are intentionally unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


PARSER_VERSION = "phase44p0_pixiv_filename_prior_v1"
PIXIV_PRIOR_PATTERN = r"(?<!\d)(?P<pixiv_work_id>[1-9]\d{5,11})_p(?P<page_index>\d+)(?!\d)"
PIXIV_PRIOR_RE = re.compile(PIXIV_PRIOR_PATTERN)


@dataclass(frozen=True, order=True)
class PixivFilenamePrior:
    work_id: str
    page_index: int
    source_field: str
    token: str


def extract_pixiv_filename_prior_from_text(value: str | None) -> list[dict[str, object]]:
    """Return the exact legacy P0 projection for one text value."""

    text_value = str(value or "")
    return [
        {
            "pixiv_work_id": match.group("pixiv_work_id"),
            "page_index": int(match.group("page_index")),
            "token": match.group(0),
            "span": [match.start(), match.end()],
        }
        for match in PIXIV_PRIOR_RE.finditer(text_value)
    ]


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
