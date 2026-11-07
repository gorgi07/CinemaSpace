"""Same result set: server query vs download-all/client filtering. Cloud Explain optional."""
import argparse
import dataclasses
import json
import os
import random
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter
from google.cloud.firestore_v1.query_profile import ExplainOptions

COLLECTION = 'notifications_experiment'


def generate(count, seed=42):
    rng = random.Random(seed)
    for i in range(count):
        yield f'lab-{i:08d}', dict(user_id=rng.randrange(100),
            status=rng.choice(['pending', 'processing', 'sent', 'failed']),
            category_slug=rng.choice(['booking', 'general', 'schedule', 'promotions']),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i // 3),
            subject=f'Lab {i}', body='Generated index experiment')


def seed_data(client, count):
    coll = client.collection(COLLECTION)
    # Replace only this dedicated experiment collection, never production notifications.
    refs = [s.reference for s in coll.stream()]
    for start in range(0, len(refs), 400):
        batch = client.batch()
        for ref in refs[start:start+400]:
            batch.delete(ref)
        batch.commit()
    batch = client.batch()
    for i, (key, data) in enumerate(generate(count), 1):
        batch.set(coll.document(key), data)
        if i % 400 == 0:
            batch.commit()
            batch = client.batch()
    if count % 400:
        batch.commit()


def query_for(coll, limit):
    return (coll.where(filter=FieldFilter('status', '==', 'sent'))
            .where(filter=FieldFilter('category_slug', '==', 'booking'))
            .order_by('created_at', direction=firestore.Query.DESCENDING)
            .order_by('__name__', direction=firestore.Query.DESCENDING).limit(limit))


def client_filter(docs, limit):
    return sorted((d for d in docs if d.to_dict().get('status') == 'sent'
                   and d.to_dict().get('category_slug') == 'booking'
                   and 'created_at' in d.to_dict()),
                  key=lambda d: (d.to_dict()['created_at'], d.id), reverse=True)[:limit]


def experiment(client, repeats=5, limit=30, explain=False):
    coll = client.collection(COLLECTION)
    query = query_for(coll, limit)
    timings = {'server_query_ms': [], 'download_and_filter_ms': []}
    transferred = {}
    for iteration in range(repeats + 1):
        results = {}
        # Alternate order to reduce warm-cache/order bias; first iteration is warm-up.
        for kind in (('server', 'client') if iteration % 2 == 0 else ('client', 'server')):
            start = time.perf_counter()
            docs = list(query.stream()) if kind == 'server' else list(coll.stream())
            results[kind] = docs if kind == 'server' else client_filter(docs, limit)
            elapsed = (time.perf_counter() - start) * 1000
            transferred[kind] = len(docs)
            if iteration:
                timings['server_query_ms' if kind == 'server' else 'download_and_filter_ms'].append(elapsed)
        if [d.id for d in results['server']] != [d.id for d in results['client']]:
            raise AssertionError('Different ordered results')
    report = dict(mode='emulator' if os.getenv('FIRESTORE_EMULATOR_HOST') else 'cloud',
                  project=client.project, collection=COLLECTION, generator_seed=42,
                  query={'status': 'sent', 'category_slug': 'booking',
                         'order': ['created_at DESC', '__name__ DESC'], 'limit': limit},
                  identical_results=True, returned=len(results['server']),
                  documents_transferred=transferred, samples=timings,
                  median_ms={k: statistics.median(v) for k, v in timings.items()},
                  index_usage_confirmed=False, composite_index_confirmed=False)
    if explain:
        if os.getenv('FIRESTORE_EMULATOR_HOST'):
            raise ValueError('Emulator cannot confirm index usage; Explain requires Cloud')
        stream = query.stream(explain_options=ExplainOptions(analyze=True))
        list(stream)
        metrics = stream.get_explain_metrics()
        report['query_explain'] = dict(plan_summary=dataclasses.asdict(metrics.plan_summary),
                                       execution_stats=dataclasses.asdict(metrics.execution_stats))
        # Report actual planner output; do not infer a composite index from elapsed time.
        report['index_usage_confirmed'] = bool(metrics.plan_summary.indexes_used)
        report['composite_index_confirmed'] = any(
            all(field in str(index.get('properties', '')) for field in ('status', 'category_slug', 'created_at'))
            for index in metrics.plan_summary.indexes_used)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--limit', type=int, default=30)
    parser.add_argument('--explain', action='store_true')
    parser.add_argument('--allow-cloud-writes', action='store_true')
    parser.add_argument('--output', default='index-results.json')
    args = parser.parse_args()
    if args.repeats < 1 or args.limit < 1 or (args.seed is not None and args.seed < 1):
        parser.error('seed/repeats/limit must be positive')
    if args.explain and os.getenv('FIRESTORE_EMULATOR_HOST'):
        parser.error('Query Explain requires Cloud, not emulator')
    if args.seed is not None and not os.getenv('FIRESTORE_EMULATOR_HOST') and not args.allow_cloud_writes:
        parser.error('Cloud seeding requires --allow-cloud-writes on a test project')
    client = firestore.Client(project=args.project)
    if args.seed is not None:
        seed_data(client, args.seed)
    report = experiment(client, args.repeats, args.limit, args.explain)
    Path(args.output).write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    print(json.dumps(report, indent=2, default=str))
