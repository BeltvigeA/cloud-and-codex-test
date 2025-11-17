import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class FakeDocumentSnapshot:
    def __init__(self, docId, data):
        self.id = docId
        self._data = dict(data)

    def to_dict(self):
        return dict(self._data)


class FakePrinterStatusQuery:
    def __init__(self, documents):
        self._documents = list(documents)

    def where(self, filter=None):  # noqa: A002 - match Firestore API
        if filter is None:
            return FakePrinterStatusQuery(self._documents)
        field = getattr(filter, 'field_path', None)
        operator = getattr(filter, 'op_string', '==')
        value = getattr(filter, 'value', None)
        filtered = []
        for snapshot in self._documents:
            snapshotValue = snapshot.to_dict().get(field)
            if operator == '==' and snapshotValue == value:
                filtered.append(snapshot)
            elif operator == '>=' and snapshotValue is not None and snapshotValue >= value:
                filtered.append(snapshot)
        return FakePrinterStatusQuery(filtered)

    def order_by(self, field, direction=None):
        isDescending = direction == getattr(main.firestore.Query, 'DESCENDING', 'DESCENDING')

        def sortKey(snapshot):
            metadata = snapshot.to_dict()
            value = metadata.get(field)
            return (value is None, value)

        sortedSnapshots = sorted(self._documents, key=sortKey, reverse=isDescending)
        return FakePrinterStatusQuery(sortedSnapshots)

    def limit(self, size):
        return FakePrinterStatusQuery(self._documents[:size])

    def stream(self):
        return list(self._documents)


class FakeFirestoreClient:
    def __init__(self, documents):
        self._documents = list(documents)

    def collection(self, name):  # noqa: A003 - match Firestore API
        assert name == main.firestoreCollectionPrinterStatus
        return FakePrinterStatusQuery(self._documents)


@pytest.fixture(autouse=True)
def _reset_api_keys(monkeypatch):
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'test-key'})


def _patch_firestore(monkeypatch, snapshots):
    fakeClient = FakeFirestoreClient(snapshots)
    fakeBundle = SimpleNamespace(firestoreClient=fakeClient)
    monkeypatch.setattr(main, '_loadClientsOrError', lambda: (fakeBundle, None))


def test_recipient_status_history_filters_and_serializes(monkeypatch):
    now = datetime.now(timezone.utc)
    snapshots = [
        FakeDocumentSnapshot(
            'status-new',
            {
                'recipientId': 'recipient-abc',
                'printerSerial': 'printer-1',
                'status': 'printing',
                'timestamp': now,
            },
        ),
        FakeDocumentSnapshot(
            'status-old',
            {
                'recipientId': 'recipient-abc',
                'printerSerial': 'printer-1',
                'status': 'idle',
                'timestamp': now - timedelta(minutes=5),
            },
        ),
        FakeDocumentSnapshot(
            'status-other',
            {
                'recipientId': 'other',
                'printerSerial': 'printer-2',
                'status': 'ready',
                'timestamp': now,
            },
        ),
    ]
    _patch_firestore(monkeypatch, snapshots)

    with main.app.test_client() as client:
        response = client.get(
            '/api/recipients/recipient-abc/status',
            headers={'X-API-Key': 'test-key'},
            query_string={'printerSerial': 'printer-1', 'limit': '5'},
        )

    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert payload['ok'] is True
    assert [update['statusId'] for update in payload['updates']] == [
        'status-new',
        'status-old',
    ]
    assert payload['updates'][0]['timestamp'].endswith('+00:00')


def test_recipient_status_history_rejects_invalid_since(monkeypatch):
    _patch_firestore(monkeypatch, [])

    with main.app.test_client() as client:
        response = client.get(
            '/api/recipients/recipient-abc/status',
            headers={'X-API-Key': 'test-key'},
            query_string={'since': 'not-a-timestamp'},
        )

    assert response.status_code == 400
    payload = json.loads(response.get_data(as_text=True))
    assert payload['ok'] is False
    assert payload['error_type'] == 'ValidationError'


def test_recipient_status_latest_groups_per_printer(monkeypatch):
    now = datetime.now(timezone.utc)
    snapshots = [
        FakeDocumentSnapshot(
            'status-a-old',
            {
                'recipientId': 'recipient-abc',
                'printerSerial': 'printer-1',
                'status': 'idle',
                'timestamp': now - timedelta(minutes=3),
            },
        ),
        FakeDocumentSnapshot(
            'status-a-new',
            {
                'recipientId': 'recipient-abc',
                'printerSerial': 'printer-1',
                'status': 'printing',
                'timestamp': now,
            },
        ),
        FakeDocumentSnapshot(
            'status-b',
            {
                'recipientId': 'recipient-abc',
                'printerSerial': 'printer-2',
                'status': 'ready',
                'timestamp': now - timedelta(minutes=1),
            },
        ),
    ]
    _patch_firestore(monkeypatch, snapshots)

    with main.app.test_client() as client:
        response = client.get(
            '/api/recipients/recipient-abc/status/latest',
            headers={'X-API-Key': 'test-key'},
            query_string={'limit': '10'},
        )

    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert payload['ok'] is True
    assert set(payload['printers'].keys()) == {'printer-1', 'printer-2'}
    assert payload['printers']['printer-1']['statusId'] == 'status-a-new'
    assert payload['printers']['printer-2']['statusId'] == 'status-b'
