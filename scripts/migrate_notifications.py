"""Read-only SQL source; atomic, resumable notification imports."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from sqlalchemy import select, func, text
from google.cloud import firestore
from app.db import SessionLocal
from app.models import Notification, Category
from app.config import settings
from app.firestore_store import NotificationStore
from app.notification_migrations import from_postgres, source_matches, fingerprint


def import_one(store, note, category_name, dry_run=False):
    record = from_postgres(note, category_name)
    ref = store.collection.document(str(note.id))
    category = store.categories.document(note.category_slug)

    # Atomic create precondition + increment avoids a read/lock race between importers.
    from google.api_core.exceptions import AlreadyExists
    for attempt in range(5):
        existing = ref.get()
        if existing.exists:
            if not source_matches(existing.to_dict(), record):
                raise ValueError(f'Conflict at notification {note.id}; no overwrite performed')
            return 'skipped'
        if dry_run:
            return 'would_create'
        batch = store.client.batch()
        if not category.get().exists:
            batch.create(category, dict(slug=note.category_slug, name=category_name, active=True,
                                        updated_at=record['created_at']))
        batch.create(ref, {**record, 'migration_fingerprint': fingerprint(record)})
        batch.set(store.stats.document(record['status']), {'count': firestore.Increment(1)}, merge=True)
        try:
            batch.commit()
            return 'created'
        except AlreadyExists:
            # Another importer created the notification/category first; re-read and verify.
            continue
    raise RuntimeError(f'Concurrent import did not settle for {note.id}')



async def migrate(store, dry_run=False, page_size=200):
    counts = {'created': 0, 'skipped': 0, 'would_create': 0, 'verified': 0}
    async with SessionLocal() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        categories = dict((await db.execute(select(Category.slug, Category.name))).all())
        maximum = await db.scalar(select(func.max(Notification.id)))
        last = -1
        while maximum is not None:
            rows = (await db.scalars(select(Notification).where(Notification.id > last,
                    Notification.id <= maximum).order_by(Notification.id).limit(page_size))).all()
            if not rows:
                break
            for note in rows:
                if note.category_slug not in categories:
                    raise ValueError(f'Unknown SQL category {note.category_slug}')
                expected = from_postgres(note, categories[note.category_slug])
                result = import_one(store, note, categories[note.category_slug], dry_run)
                counts[result] += 1
                if not dry_run:
                    actual = store.collection.document(str(note.id)).get().to_dict()
                    if not source_matches(actual, expected):
                        raise AssertionError(f'Verification failed: {note.id}')
                    if result == 'created' and any(actual.get(k) != v for k, v in expected.items()):
                        raise AssertionError(f'Payload mismatch: {note.id}')
                    counts['verified'] += 1
            last = rows[-1].id
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(migrate(NotificationStore(settings.firestore_project_id), args.dry_run))))
