"""Synchronous Firestore repository, isolated behind asyncio.to_thread at API boundary.
Both Emulator and Cloud use the same client and exactly the same business code.
"""
import os
import uuid
from datetime import datetime, timezone
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter
from .notification_migrations import upgrade


def utc(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try: return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError: return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def iso(value):
    return utc(value).isoformat()


class NotificationStore:
    def __init__(self, project_id: str):
        self.client = firestore.Client(project=project_id)
        self.collection = self.client.collection('notifications')
        self.categories = self.client.collection('notification_categories')
        self.stats = self.client.collection('notification_stats')

    def seed_categories(self):
        for slug, name in [('booking', 'Бронирования'), ('schedule', 'Расписание'), ('promotions', 'Акции'), ('general', 'Общие')]:
            ref = self.categories.document(slug)
            if not ref.get().exists:
                ref.set({'slug': slug, 'name': name, 'active': True, 'updated_at': datetime.now(timezone.utc)})

    def get_categories(self):
        return [{'slug': s.id, 'name': s.to_dict()['name']} for s in self.categories.stream() if s.to_dict().get('active', True)]

    def get(self, note_id):
        doc = self.collection.document(str(note_id)).get()
        return self._view(doc) if doc.exists else None

    def _view(self, doc):
        data = doc.to_dict()
        return {'id': doc.id, 'user_id': data['user_id'], 'category_slug': data['category_slug'],
                'subject': data['subject'], 'body': data['body'],
                'created_at': iso(data.get('created_at')), 'read': bool(data.get('read_at')),
                'email_status': data.get('email_status', 'pending'), 'status': data.get('status', 'pending'),
                'stages': [{**s, 'at': iso(s.get('at'))} for s in data.get('stages', [])],
                'category_name': data.get('category_name', ''),
                'schema_version': data.get('schema_version', 2)}

    def create(self, user_id, category, subject, body, *, note_id=None, created_at=None, read_at=None, email_status='pending'):
        note_id = str(note_id or uuid.uuid4())
        ref = self.collection.document(note_id)
        category_doc = self.categories.document(category).get()
        if not category_doc.exists: raise ValueError('Категория не существует')
        now = utc(created_at)
        stage = {'type': 'created', 'status': 'completed', 'at': now, 'description': 'Уведомление создано'}
        record = {'user_id': user_id, 'category_slug': category, 'category_name': category_doc.to_dict()['name'],
                  'subject': subject, 'body': body, 'created_at': now, 'updated_at': now, 'read_at': utc(read_at) if read_at else None,
                  'email_status': email_status, 'status': 'pending', 'stages': [stage], 'stage_types': ['created'], 'schema_version': 3, 'body_format': 'plain'}
        @firestore.transactional
        def apply(tx):
            snap = ref.get(transaction=tx)
            if snap.exists: return False
            stat = self.stats.document('pending')
            tx.create(ref, record)
            tx.set(stat, {'count': firestore.Increment(1)}, merge=True)
            return True
        added = apply(self.client.transaction())
        return self.get(note_id), added

    def modify(self, note_id, *, changes=None, stage=None):
        ref = self.collection.document(str(note_id))
        changes = changes or {}
        permitted = {'status', 'email_status', 'read_at', 'subject', 'body', 'category_slug'}
        if not set(changes).issubset(permitted): raise ValueError('Недопустимое поле для изменения')
        @firestore.transactional
        def apply(tx):
            current = ref.get(transaction=tx)
            if not current.exists: return False
            old = current.to_dict()
            new = dict(changes)
            new.update(upgrade(old))
            if 'category_slug' in new:
                cat = self.categories.document(new['category_slug']).get(transaction=tx)
                if not cat.exists: raise ValueError('Категория не существует')
                new['category_name'] = cat.to_dict()['name']
                if new['category_slug'] != old.get('category_slug'):
                    tx.update(self.categories.document(new['category_slug']),
                              {'assignment_events': firestore.Increment(1), 'updated_at': datetime.now(timezone.utc)})
            entries = list(old.get('stages', []))
            stage_types = list(old.get('stage_types', []))
            if 'status' in new and new['status'] != old.get('status'):
                if new['status'] not in {'pending', 'processing', 'sent', 'failed'}: raise ValueError('Недопустимый статус')
                previous = old.get('status', 'pending')
                tx.set(self.stats.document(previous), {'count': firestore.Increment(-1)}, merge=True)
                tx.set(self.stats.document(new['status']), {'count': firestore.Increment(1)}, merge=True)
                entries.append({'type': 'status_change', 'description': f'Состояние изменено: {previous} → {new["status"]}',
                                'status': new['status'], 'at': datetime.now(timezone.utc)})
                stage_types.append('status_change')
            if stage:
                entries.append({'type': stage['type'], 'description': stage.get('description', ''),
                                'status': stage.get('status', 'completed'), 'at': datetime.now(timezone.utc)})
                stage_types.append(stage['type'])
            if len(entries) != len(old.get('stages', [])):
                new['stages'] = entries
                new['stage_types'] = list(dict.fromkeys(stage_types))
            new['updated_at'] = datetime.now(timezone.utc)
            tx.update(ref, new)
            return True
        changed = apply(self.client.transaction())
        return self.get(note_id) if changed else None

    def claim_retry(self, note_id):
        """Atomically claim a failed email for retry; prevent two admins sending it twice."""
        ref = self.collection.document(str(note_id))
        @firestore.transactional
        def apply(tx):
            snap = ref.get(transaction=tx)
            if not snap.exists:
                return None
            old = snap.to_dict()
            if old.get('status') != 'failed':
                raise ValueError('Повторить можно только уведомление со статусом «Ошибка»')
            at = datetime.now(timezone.utc)
            tx.update(ref, {'status': 'processing', 'email_status': 'processing',
                            'updated_at': at,
                            'stages': firestore.ArrayUnion([{'type':'retry', 'status':'processing',
                                 'description':'Повторная попытка SMTP-отправки', 'at':at}]),
                            'stage_types': firestore.ArrayUnion(['retry'])})
            tx.set(self.stats.document('failed'), {'count': firestore.Increment(-1)}, merge=True)
            tx.set(self.stats.document('processing'), {'count': firestore.Increment(1)}, merge=True)
            return {'id': str(note_id), 'user_id': old['user_id'], 'subject': old['subject'], 'body': old['body']}
        return apply(self.client.transaction())

    def delete(self, note_id):
        ref = self.collection.document(str(note_id))
        @firestore.transactional
        def apply(tx):
            snap = ref.get(transaction=tx)
            if not snap.exists: return False
            tx.delete(ref)
            tx.set(self.stats.document(snap.to_dict().get('status', 'pending')), {'count': firestore.Increment(-1)}, merge=True)
            return True
        return apply(self.client.transaction())

    def search(self, *, user_id=None, status=None, category_slug=None, stage_type=None, limit=30, cursor=None, order='desc'):
        q = self.collection
        if user_id is not None: q = q.where(filter=FieldFilter('user_id', '==', user_id))
        if status: q = q.where(filter=FieldFilter('status', '==', status))
        if category_slug: q = q.where(filter=FieldFilter('category_slug', '==', category_slug))
        if stage_type: q = q.where(filter=FieldFilter('stage_types', 'array_contains', stage_type))
        q = q.order_by('created_at', direction=firestore.Query.DESCENDING if order == 'desc' else firestore.Query.ASCENDING)
        q = q.order_by('__name__', direction=firestore.Query.DESCENDING if order == 'desc' else firestore.Query.ASCENDING)
        if cursor:
            last = self.collection.document(cursor).get()
            if not last.exists: raise ValueError('Неверный курсор страницы')
            q = q.start_after(last)
        docs = list(q.limit(min(max(limit, 1), 100) + 1).stream())
        has_more = len(docs) > limit
        docs = docs[:limit]
        return {'items': [self._view(x) for x in docs], 'next_cursor': docs[-1].id if has_more and docs else None}

    def stats_counts(self):
        return {name: max(0, int((self.stats.document(name).get().to_dict() or {}).get('count', 0)))
                for name in ['pending', 'processing', 'sent', 'failed']}

    def recount(self):
        counts = {'pending': 0, 'processing': 0, 'sent': 0, 'failed': 0}
        for doc in self.collection.stream():
            status = doc.to_dict().get('status', 'pending')
            counts[status] = counts.get(status, 0) + 1
        batch = self.client.batch()
        for status, count in counts.items(): batch.set(self.stats.document(status), {'count': count})
        batch.commit()
        return counts
