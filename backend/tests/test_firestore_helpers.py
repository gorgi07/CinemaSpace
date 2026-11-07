from datetime import datetime, timezone
from app.firestore_store import utc, iso

def test_migration_converts_iso_string_to_real_datetime():
    value = utc('2026-10-26T12:30:00Z')
    assert isinstance(value, datetime)
    assert value.tzinfo is not None
    assert value.utcoffset().total_seconds() == 0

def test_iso_datetime_roundtrip():
    value = datetime(2026, 10, 26, tzinfo=timezone.utc)
    assert utc(iso(value)) == value
