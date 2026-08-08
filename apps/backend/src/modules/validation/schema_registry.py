"""
Schema registry for the Data Validation module.

Required columns and their value validators are data (a registry), not
logic scattered through if/else branches - adding a new source type, or
adjusting which columns are required, is a change to this file only.

The exact column sets below are an MVP assumption, not a confirmed schema:
real Foodics exports and each delivery platform's settlement report format
haven't been sampled yet (flagged as a Phase 1 risk - export schemas drift
and vary by platform). Treat SOURCE_TYPE_SCHEMAS as the seam to update once
real samples are available, not as a finished specification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable

from modules.imports.models import SourceType

from .models import IssueCode

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
)


def normalize_header(header: str) -> str:
    """Case/whitespace/punctuation-tolerant form used only for matching a
    file's actual header text against the accepted spellings below - real
    exports are inconsistent about capitalization and spacing."""
    return re.sub(r"[\s_-]+", "", header.strip().lower())


def is_non_empty(value: str) -> bool:
    return value is not None and value.strip() != ""


def parse_decimal(value: str) -> Decimal | None:
    """Returns the parsed Decimal, or None if unparseable. Used directly by
    both Data Validation (via is_valid_decimal) and Data Normalization -
    one parsing rule, not two copies that could quietly drift apart."""
    if not is_non_empty(value):
        return None
    cleaned = value.strip().replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def is_valid_decimal(value: str) -> bool:
    return parse_decimal(value) is not None


def parse_date(value: str) -> datetime | None:
    """Returns the parsed (naive) datetime, or None if unparseable. Same
    reuse rationale as parse_decimal above."""
    if not is_non_empty(value):
        return None
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def is_valid_date(value: str) -> bool:
    return parse_date(value) is not None


@dataclass(frozen=True)
class ColumnSpec:
    field_name: str
    accepted_headers: tuple[str, ...]
    validator: Callable[[str], bool]
    issue_code: IssueCode
    # Defaults True so every existing entry below stays required without
    # being touched - only order_status (added for cancellation
    # detection) sets this False. An optional column that's absent from
    # a file is not a validation failure; one that's present still gets
    # its values checked by `validator` like any other column.
    is_required: bool = True

    def matches_header(self, header: str) -> bool:
        normalized = normalize_header(header)
        return any(normalized == normalize_header(h) for h in self.accepted_headers)

    @property
    def display_name(self) -> str:
        """Human-readable label for error messages, e.g. 'order_id' -> 'Order Id'.
        Not grammatically perfect, but always understandable - and it comes
        from the schema itself, so there's exactly one place to improve it."""
        return self.field_name.replace("_", " ").title()


SOURCE_TYPE_SCHEMAS: dict[SourceType, tuple[ColumnSpec, ...]] = {
    SourceType.POS_EXPORT: (
        ColumnSpec(
            field_name="order_id",
            accepted_headers=("order_id", "order id", "order reference", "reference"),
            validator=is_non_empty,
            issue_code=IssueCode.EMPTY_REQUIRED_FIELD,
        ),
        ColumnSpec(
            field_name="order_time",
            accepted_headers=("order_time", "order date", "order datetime", "date"),
            validator=is_valid_date,
            issue_code=IssueCode.INVALID_DATE,
        ),
        ColumnSpec(
            field_name="amount",
            accepted_headers=("amount", "total", "order total", "net amount"),
            validator=is_valid_decimal,
            issue_code=IssueCode.INVALID_NUMBER,
        ),
        ColumnSpec(
            field_name="order_status",
            accepted_headers=("order_status", "order status", "status"),
            # Deliberately permissive: this column's whole purpose is to
            # catch a cancellation/refund when a restaurant's POS export
            # happens to include one, in whatever spelling their system
            # uses - "CANCELLED", "Refunded", "voided", anything.
            # Normalization decides what the value means; validation's
            # only job for an optional column is "did a value get read",
            # not "is it one of the values we anticipated".
            validator=lambda _value: True,
            issue_code=IssueCode.INVALID_STATUS_VALUE,
            is_required=False,
        ),
    ),
    SourceType.PLATFORM_SETTLEMENT: (
        ColumnSpec(
            field_name="order_id",
            accepted_headers=("order_id", "order id", "order reference", "reference"),
            validator=is_non_empty,
            issue_code=IssueCode.EMPTY_REQUIRED_FIELD,
        ),
        ColumnSpec(
            field_name="settlement_date",
            accepted_headers=("settlement_date", "settlement date", "date", "payout date"),
            validator=is_valid_date,
            issue_code=IssueCode.INVALID_DATE,
        ),
        ColumnSpec(
            field_name="gross_amount",
            accepted_headers=("gross_amount", "gross amount", "order total", "amount"),
            validator=is_valid_decimal,
            issue_code=IssueCode.INVALID_NUMBER,
        ),
        ColumnSpec(
            field_name="commission_amount",
            accepted_headers=("commission_amount", "commission", "platform fee", "fee"),
            validator=is_valid_decimal,
            issue_code=IssueCode.INVALID_NUMBER,
        ),
    ),
}


def schema_for(source_type: SourceType) -> tuple[ColumnSpec, ...]:
    return SOURCE_TYPE_SCHEMAS[source_type]
