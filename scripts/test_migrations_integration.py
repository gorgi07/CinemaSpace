"""Destructive fixtures ONLY in an explicitly named test DB and demo emulator project."""
import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from sqlalchemy import select
from app.config import settings
from app.db import engine, SessionLocal
from app.models import Base, User, Category, Notification
from app.firestore_store import NotificationStore
from app.notification_migrations import from_postgres
from migrate_notifications import migrate, import_one
from migrate_firestore import migrate as upgrade_all


async def main():
    if (not os.getenv('FIRESTORE_EMULATOR_HOST') or settings.firestore_project_id != 'demo-cinema-migrations'
            or engine.url.database != 'cinema_migration_test'):
        raise RuntimeError('Requires emulator, project demo-cinema-migrations and DB cinema_migration_test')
    store = NotificationStore(settings.firestore_project_id)
    for name in ('notifications', 'notification_categories', 'notification_stats'):
        for doc in store.client.collection(name).stream():
            doc.reference.delete()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        db.add(User(id=1, email='fixture@example.test', password_hash='not-used', full_name='Fixture'))
        db.add(Category(id=1, slug='custom', name='Тестовая категория'))
        await db.flush()
        for i in range(1, 121):
            at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)
            db.add(Notification(id=i, user_id=1, category_slug='custom', subject=f'Тест {i}',
                body=f'Строка {i}\nЮникод', created_at=at, read_at=at if i % 2 else None,
                email_status=['pending', 'processing', 'sent', 'failed'][i % 4]))
        await db.commit()
    dry = await migrate(store, dry_run=True, page_size=17)
    assert dry['would_create'] == 120 and not list(store.collection.stream())
    assert not list(store.categories.stream()) and not list(store.stats.stream())
    # Simulate an interruption after 37 committed documents, then restart the CLI's engine.
    async with SessionLocal() as db:
        notes = (await db.scalars(select(Notification).order_by(Notification.id))).all()
    for note in notes[:37]:
        assert import_one(store, note, 'Тестовая категория') == 'created'
    resumed = await migrate(store, page_size=17)
    assert resumed['created'] == 83 and resumed['skipped'] == 37 and resumed['verified'] == 120
    again = await migrate(store)
    assert again['created'] == 0 and again['skipped'] == 120
    assert store.stats_counts() == dict(pending=30, processing=30, sent=30, failed=30)
    for note in notes:
        expected = from_postgres(note, 'Тестовая категория')
        actual = store.collection.document(str(note.id)).get().to_dict()
        assert all(actual[k] == v for k, v in expected.items())
        assert store.get(note.id)['body'] == note.body
    store.modify(1, changes={'body': 'live edit', 'status': 'sent'})
    await migrate(store)
    assert store.get(1)['body'] == 'live edit'
    notes[0].body = 'changed SQL source'
    try:
        import_one(store, notes[0], 'Тестовая категория')
        raise AssertionError('Expected source conflict')
    except ValueError:
        pass
    # Concurrent import of one new id increments stats exactly once.
    notes[0].id = 999
    with ThreadPoolExecutor(max_workers=4) as pool:
        concurrent = list(pool.map(lambda _: import_one(store, notes[0], 'Тестовая категория'), range(4)))
    assert concurrent.count('created') == 1 and concurrent.count('skipped') == 3
    assert store.stats_counts() == store.recount()
    from unittest.mock import patch
    before_failure = store.stats_counts()
    notes[0].id = 1000
    real_batch = store.client.batch
    def failing_batch():
        batch = real_batch()
        batch.commit = lambda: (_ for _ in ()).throw(RuntimeError('injected failure before commit'))
        return batch
    with patch.object(store.client, 'batch', failing_batch):
        try:
            import_one(store, notes[0], 'Тестовая категория')
            raise AssertionError('Expected injected failure')
        except RuntimeError as exc:
            assert 'injected failure' in str(exc)
    assert not store.collection.document('1000').get().exists
    assert store.stats_counts() == before_failure
    assert import_one(store, notes[0], 'Тестовая категория') == 'created'
    # Existing application CRUD, mixed schemas and stable pagination continue to work.
    store.seed_categories()
    created, added = store.create(1, 'general', 'CRUD fixture', 'body', note_id='crud-fixture')
    assert added and created['schema_version'] == 3
    assert store.create(1, 'general', 'ignored', 'ignored', note_id='crud-fixture')[1] is False
    from google.cloud import firestore
    store.collection.document('crud-fixture').update({'schema_version': 2, 'body_format': firestore.DELETE_FIELD})
    assert store.get('crud-fixture')['body'] == 'body'
    updated = store.modify('crud-fixture', changes={'body': 'changed', 'category_slug': 'custom', 'status': 'sent'},
                           stage={'type': 'tested'})
    assert updated['body'] == 'changed' and updated['schema_version'] == 3
    first = store.search(user_id=1, limit=10)
    second = store.search(user_id=1, limit=10, cursor=first['next_cursor'])
    assert len(first['items']) == len(second['items']) == 10
    assert not {d['id'] for d in first['items']} & {d['id'] for d in second['items']}
    assert store.delete('crud-fixture') and store.get('crud-fixture') is None
    assert store.stats_counts() == store.recount()
    # A document from the old importer has no source fingerprint: reject instead of overwrite.
    store.collection.document('collision').set({'body': 'keep'})
    notes[0].id = 'collision'
    try:
        import_one(store, notes[0], 'Тестовая категория')
        raise AssertionError('Expected destination conflict')
    except ValueError:
        pass
    assert store.collection.document('collision').get().to_dict() == {'body': 'keep'}
    store.collection.document('collision').delete()
    # 205 v2 docs cross page boundaries and include equal timestamps/unknown fields.
    for i in range(205):
        store.collection.document(f'v2-{i:04d}').set({
            'schema_version': 2, 'body': f'old {i}', 'status': 'pending', 'extra': {'keep': i}})
    dry_upgrade = upgrade_all(store.client, True, page_size=31)
    assert dry_upgrade['would_update'] == 205
    for i in range(17):
        store.collection.document(f'v2-{i:04d}').update({'schema_version': 3, 'body_format': 'plain'})
    upgraded = upgrade_all(store.client, page_size=31)
    assert upgraded['updated'] == 188
    assert upgrade_all(store.client)['updated'] == 0
    for i in range(205):
        data = store.collection.document(f'v2-{i:04d}').get().to_dict()
        assert data == dict(schema_version=3, body_format='plain', body=f'old {i}', status='pending', extra={'keep': i})
    # Old writer can still change legacy fields after migration.
    store.collection.document('v2-0000').update({'body': 'old writer update'})
    assert upgrade_all(store.client)['updated'] == 0
    future = store.collection.document('future')
    future.set({'schema_version': 999, 'body': 'keep'})
    try:
        upgrade_all(store.client)
        raise AssertionError('Expected future schema rejection')
    except ValueError:
        pass
    assert future.get().to_dict()['schema_version'] == 999
    future.delete()
    report = dict(sql_generated=120, dry_run=dry, resume=resumed, repeat=again,
        concurrent=concurrent, firestore_generated_v2=205, upgrade_dry_run=dry_upgrade,
        upgrade_resume=upgraded, checks='payloads, timestamps, stats, conflicts, live edits, paging, future schema, old writer: passed')
    report['injected_failure_recovery'] = 'passed'
    report['application_crud_and_paging'] = 'passed'
    output = Path(__file__).resolve().parents[1] / 'validation' / 'integration-results.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
