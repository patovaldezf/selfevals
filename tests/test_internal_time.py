from datetime import UTC, datetime, timezone

import pytest

from bootstrap._internal.time import ensure_utc, utc_now


def test_utc_now_is_tz_aware() -> None:
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(now)


def test_ensure_utc_rejects_naive() -> None:
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 5, 16, 12, 0, 0))


def test_ensure_utc_converts_other_tz() -> None:
    other = timezone(offset=datetime.now().astimezone().utcoffset() or UTC.utcoffset(None))
    dt = datetime(2026, 5, 16, 12, 0, 0, tzinfo=other)
    result = ensure_utc(dt)
    assert result.tzinfo == UTC
    assert result.timestamp() == dt.timestamp()
