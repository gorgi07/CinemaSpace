from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from app.notification_migrations import from_postgres, timestamp, upgrade, fingerprint, source_matches


def note(**changes):
    return SimpleNamespace(**(dict(id=1, user_id=2, category_slug='custom', subject='Тема', body='Текст',
        created_at='2026-11-05T15:30:00+03:00', read_at=None, email_status='sent') | changes))


@pytest.mark.parametrize('status', ['pending', 'processing', 'sent', 'failed'])
def test_mapping(status):
    record = from_postgres(note(email_status=status), 'Своя категория')
    assert record['status'] == record['email_status'] == status
    assert record['created_at'] == datetime(2026, 11, 5, 12, 30, tzinfo=timezone.utc)
    assert record['category_name'] == 'Своя категория'


@pytest.mark.parametrize('value', [None, '', 'invalid', 123])
def test_invalid_timestamp_rejected(value):
    with pytest.raises((ValueError, TypeError)):
        timestamp(value)


def test_naive_timestamp_explicit_utc():
    assert timestamp(datetime(2026, 1, 1)).tzinfo == timezone.utc


def test_additive_upgrade_and_old_writer():
    old = dict(schema_version=2, body='old', arbitrary={'keep': True}, status='sent')
    new = old | upgrade(old)
    assert {k: new[k] for k in old if k != 'schema_version'} == {k: v for k, v in old.items() if k != 'schema_version'}
    assert upgrade(new) == {}
    new['body'] = 'written by v2 application'
    assert new['body_format'] == 'plain'


@pytest.mark.parametrize('version', [1, 4, '2', True])
def test_unknown_schema_rejected(version):
    with pytest.raises(ValueError):
        upgrade({'schema_version': version})


def test_import_fingerprint_preserves_live_edits_and_detects_source_change():
    expected = from_postgres(note(), 'Custom')
    live = expected | {'migration_fingerprint': fingerprint(expected), 'body': 'live edit', 'status': 'failed'}
    assert source_matches(live, expected)
    assert not source_matches(live, from_postgres(note(body='SQL changed'), 'Custom'))
    assert not source_matches(expected, expected)  # no provenance: conflict, not silent adoption


def test_invalid_status():
    with pytest.raises(ValueError):
        from_postgres(note(email_status='unknown'), 'Custom')
