from __future__ import annotations

from datetime import date, datetime, timezone

import jdatetime


def _to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=None)


def format_jalali_datetime(dt: datetime) -> str:
    """
    Format Jalali (Shamsi) datetime using jdatetime, based on UTC time.
    """
    j = jdatetime.datetime.fromgregorian(datetime=_to_utc_naive(dt))
    return j.strftime("%Y/%m/%d %H:%M:%S")


def format_jalali_date(d: date) -> str:
    """
    Format Jalali (Shamsi) date using jdatetime.
    """
    # Use noon to avoid any timezone edge cases, then drop time via strftime.
    g = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
    j = jdatetime.datetime.fromgregorian(datetime=_to_utc_naive(g))
    return j.strftime("%Y/%m/%d")


def format_dual_date(d: date) -> str:
    return f"{d.isoformat()} ({format_jalali_date(d)})"


def format_dual_datetime_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    g = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    j = format_jalali_datetime(dt)
    return f"{g} ({j})"

