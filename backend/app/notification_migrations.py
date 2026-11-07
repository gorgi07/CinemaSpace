"""Strict, deterministic notification transformations; no network or clock access."""
from datetime import datetime, timezone
import hashlib
import json

CURRENT_VERSION = 3
STATUSES = {'pending', 'processing', 'sent', 'failed'}


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime):
        raise ValueError('Expected a timestamp')
    # Legacy PostgreSQL timestamps without timezone are explicitly interpreted as UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def upgrade(data):
    """v2 -> v3 is additive: old clients keep reading/writing all existing fields."""
    version = data.get('schema_version', 2)
    if type(version) is not int or version not in (2, 3):
        raise ValueError(f'Unsupported schema version: {version}')
    if version == 3:
        if data.get('body_format') != 'plain':
            raise ValueError('Invalid v3 body_format')
        return {}
    return {'schema_version': 3, 'body_format': 'plain'}


def from_postgres(note, category_name):
    status = note.email_status
    if status not in STATUSES:
        raise ValueError(f'Unsupported email status: {status}')
    at = timestamp(note.created_at)
    record = dict(user_id=note.user_id, category_slug=note.category_slug,
                  category_name=category_name, subject=note.subject, body=note.body,
                  created_at=at, updated_at=at,
                  read_at=timestamp(note.read_at) if note.read_at is not None else None,
                  email_status=status, status=status,
                  stages=[dict(type='created', status='completed', at=at,
                               description='Уведомление создано')],
                  stage_types=['created'], schema_version=3, body_format='plain',
                  migration_source={'system': 'postgresql', 'table': 'notifications', 'id': note.id})
    return record


def source_matches(existing, expected):
    # Mutable application fields (read/status/body) may change after import.
    # Keep an immutable source digest so reruns detect changed SQL source safely.
    return (existing.get('migration_source') == expected['migration_source']
            and existing.get('migration_fingerprint') == fingerprint(expected))


def fingerprint(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                    default=lambda v: timestamp(v).isoformat()).encode()).hexdigest()
