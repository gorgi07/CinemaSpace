"""Resumable per-document v2 -> v3 migration with transaction retries."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from google.cloud import firestore
from app.config import settings
from app.notification_migrations import upgrade


def migrate(client, dry_run=False, collection='notifications', page_size=200):
    counts = {'updated': 0, 'skipped': 0, 'would_update': 0}
    coll = client.collection(collection)
    cursor = None
    while True:
        query = coll.order_by('__name__').limit(page_size)
        if cursor is not None:
            query = query.start_after(cursor)
        docs = list(query.stream())
        if not docs:
            return counts
        for doc in docs:
            @firestore.transactional
            def apply(tx):
                current = doc.reference.get(transaction=tx)
                if not current.exists:
                    return 'skipped'
                patch = upgrade(current.to_dict())
                if not patch:
                    return 'skipped'
                if not dry_run:
                    tx.update(doc.reference, patch)
                return 'would_update' if dry_run else 'updated'
            counts[apply(client.transaction())] += 1
        cursor = docs[-1]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(migrate(firestore.Client(project=settings.firestore_project_id), args.dry_run)))
