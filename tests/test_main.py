import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

fakeFlaskModule = ModuleType('flask')
fakeWerkzeugModule = ModuleType('werkzeug')
fakeWerkzeugUtilsModule = ModuleType('werkzeug.utils')


class DummyFlask:
    def __init__(self, _name):
        self.name = _name

    def route(self, _rule, methods=None):  # pylint: disable=unused-argument
        def decorator(function):
            return function

        return decorator


def dummyJsonify(payload):
    return payload


def secureFilename(value):
    sanitized = ''.join(
        character for character in value if character.isalnum() or character in {'.', '_', '-'}
    )
    return sanitized.strip(' .')


class FakeRequest(SimpleNamespace):
    def __init__(self):
        super().__init__(files={}, form={}, args={}, headers={})
        self._jsonPayload = None
        self.is_json = False
        self.method = 'POST'

    def set_json(self, payload):
        self._jsonPayload = payload
        self.is_json = True

    def clear_json(self):
        self._jsonPayload = None
        self.is_json = False

    def get_json(self):
        return self._jsonPayload


fakeRequest = FakeRequest()
fakeFlaskModule.Flask = DummyFlask
fakeFlaskModule.jsonify = dummyJsonify
fakeFlaskModule.request = fakeRequest
fakeWerkzeugModule.utils = fakeWerkzeugUtilsModule
fakeWerkzeugUtilsModule.secure_filename = secureFilename
sys.modules['flask'] = fakeFlaskModule
sys.modules['werkzeug'] = fakeWerkzeugModule
sys.modules['werkzeug.utils'] = fakeWerkzeugUtilsModule

googleModule = ModuleType('google')
cloudModule = ModuleType('google.cloud')
firestoreModule = ModuleType('google.cloud.firestore')
firestoreModule.SERVER_TIMESTAMP = object()


class DummyFirestoreClient:
    def __init__(self, *args, **kwargs):  # pylint: disable=unused-argument
        pass

    def collection(self, *_args, **_kwargs):
        raise NotImplementedError


firestoreModule.Client = DummyFirestoreClient
firestoreV1Module = ModuleType('google.cloud.firestore_v1')
firestoreV1Module.DELETE_FIELD = object()
storageModule = ModuleType('google.cloud.storage')


class DummyStorageClient:
    def __init__(self, *args, **kwargs):  # pylint: disable=unused-argument
        pass

    def bucket(self, *_args, **_kwargs):
        raise NotImplementedError


storageModule.Client = DummyStorageClient
kmsModule = ModuleType('google.cloud.kms_v1')


class DummyKmsClient:
    def __init__(self, *args, **kwargs):  # pylint: disable=unused-argument
        pass

    def crypto_key_path(self, *args, **kwargs):  # pylint: disable=unused-argument
        return 'projects/test/locations/test/keyRings/test/cryptoKeys/test'


kmsModule.KeyManagementServiceClient = DummyKmsClient
secretmanagerModule = ModuleType('google.cloud.secretmanager')


class DummySecretManagerClient:
    def __init__(self, payload=b''):
        self.payload = payload
        self.requestedNames = []

    def access_secret_version(self, name):
        self.requestedNames.append(name)
        return SimpleNamespace(payload=SimpleNamespace(data=self.payload))


secretmanagerModule.SecretManagerServiceClient = DummySecretManagerClient
exceptionsModule = ModuleType('google.api_core.exceptions')


class DummyGoogleApiCallError(Exception):
    def __init__(self, message='error'):
        super().__init__(message)
        self.message = message


exceptionsModule.GoogleAPICallError = DummyGoogleApiCallError
exceptionsModule.Forbidden = type('DummyForbidden', (DummyGoogleApiCallError,), {})
exceptionsModule.PermissionDenied = type(
    'DummyPermissionDenied', (DummyGoogleApiCallError,), {}
)
exceptionsModule.Unauthorized = type('DummyUnauthorized', (DummyGoogleApiCallError,), {})
apiCoreModule = ModuleType('google.api_core')
apiCoreModule.exceptions = exceptionsModule

authModule = ModuleType('google.auth')
authExceptionsModule = ModuleType('google.auth.exceptions')


class DummyGoogleAuthError(Exception):
    pass


authExceptionsModule.GoogleAuthError = DummyGoogleAuthError
authModule.exceptions = authExceptionsModule

sys.modules['google'] = googleModule
sys.modules['google.cloud'] = cloudModule
sys.modules['google.cloud.firestore'] = firestoreModule
sys.modules['google.cloud.firestore_v1'] = firestoreV1Module
sys.modules['google.cloud.storage'] = storageModule
sys.modules['google.cloud.kms_v1'] = kmsModule
sys.modules['google.cloud.secretmanager'] = secretmanagerModule
sys.modules['google.api_core'] = apiCoreModule
sys.modules['google.api_core.exceptions'] = exceptionsModule
sys.modules['google.auth'] = authModule
sys.modules['google.auth.exceptions'] = authExceptionsModule

googleModule.cloud = cloudModule
cloudModule.firestore = firestoreModule
cloudModule.storage = storageModule
cloudModule.kms_v1 = kmsModule
cloudModule.secretmanager = secretmanagerModule
googleModule.auth = authModule

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('GCP_PROJECT_ID', 'test-project')
os.environ.setdefault('GCS_BUCKET_NAME', 'test-bucket')
os.environ.setdefault('KMS_KEY_RING', 'test-key-ring')
os.environ.setdefault('KMS_KEY_NAME', 'test-key')
os.environ.setdefault('KMS_LOCATION', 'test-location')
import main  # noqa: E402


class MockBlob:
    def upload_from_file(self, fileObj):
        fileObj.read()

    def generate_signed_url(self, **_kwargs):
        return 'https://example.com/signed-url'


class MockUploadFile:
    def __init__(self, data, filename, mimetype='application/octet-stream'):
        self.stream = BytesIO(data)
        self.filename = filename
        self.mimetype = mimetype

    def read(self, *_args, **_kwargs):
        return self.stream.read(*_args, **_kwargs)


class MockBucket:
    def blob(self, _name):
        return MockBlob()


class MockStorageClient:
    def bucket(self, _name):
        return MockBucket()


class MockEncryptClient:
    def __init__(self, plaintextResponse=None):
        self.plaintextResponse = plaintextResponse or {}

    def encrypt(self, **_kwargs):
        return SimpleNamespace(ciphertext=b'encrypted-data')

    def decrypt(self, **_kwargs):
        plaintext = json.dumps(self.plaintextResponse).encode('utf-8')
        return SimpleNamespace(plaintext=plaintext)


class MockDocument:
    def __init__(self, documentStore, docId, updateRecorder, addRecorder):
        self.documentStore = documentStore
        self.docId = docId
        self.updateRecorder = updateRecorder
        self.addRecorder = addRecorder

    def set(self, metadata):
        self.documentStore[self.docId] = metadata
        self.updateRecorder['set'] = metadata

    def update(self, payload):
        existingMetadata = dict(self.documentStore.get(self.docId, {}))
        for key, value in payload.items():
            if value is main.DELETE_FIELD:
                existingMetadata.pop(key, None)
            else:
                existingMetadata[key] = value
        self.documentStore[self.docId] = existingMetadata
        self.updateRecorder['update'].append(payload)

    def get(self, transaction=None):  # pylint: disable=unused-argument
        metadata = self.documentStore.get(self.docId)
        if metadata is None:
            return MockDocumentSnapshot(self.docId, None, exists=False)
        return MockDocumentSnapshot(self.docId, metadata)

    def collection(self, _name):
        return MockCollection([], self.documentStore, self.updateRecorder, self.addRecorder)


class MockTransaction:
    def __init__(self, documentStore, updateRecorder):
        self.documentStore = documentStore
        self.updateRecorder = updateRecorder
        self.writes = []

    def get(self, documentReference):
        return documentReference.get(transaction=self)

    def update(self, documentReference, payload):
        documentReference.update(payload)
        self.writes.append((documentReference.docId, payload))

    def commit(self):
        return self.writes


class MockQuery:
    def __init__(self, documentSnapshots, filters=None):
        if documentSnapshots is None:
            self.documentSnapshots = []
        elif isinstance(documentSnapshots, list):
            self.documentSnapshots = list(documentSnapshots)
        else:
            self.documentSnapshots = [documentSnapshots]
        self.filters = filters or []

    def where(self, field, operator, value):  # pylint: disable=unused-argument
        filteredSnapshots = []
        if operator == '==':
            def condition(metadata):
                return metadata.get(field) == value

        elif operator == 'in':
            allowedValues = set(value)

            def condition(metadata):
                return metadata.get(field) in allowedValues
        else:
            raise NotImplementedError('Only == and in filters are supported in tests')

        for snapshot in self.documentSnapshots:
            metadata = snapshot.to_dict() or {}
            if condition(metadata):
                filteredSnapshots.append(snapshot)

        return MockQuery(filteredSnapshots, self.filters + [(field, operator, value)])

    def limit(self, _count):
        return self

    def stream(self):
        return self.documentSnapshots


class MockCollection:
    def __init__(self, documentSnapshots, documentStore, updateRecorder, addRecorder):
        self.documentSnapshots = list(documentSnapshots)
        self.documentStore = documentStore
        self.updateRecorder = updateRecorder
        self.addRecorder = addRecorder

    def _currentSnapshots(self):
        if self.documentStore:
            return [
                MockDocumentSnapshot(docId, metadata)
                for docId, metadata in self.documentStore.items()
            ]
        return list(self.documentSnapshots)

    def document(self, docId):
        return MockDocument(self.documentStore, docId, self.updateRecorder, self.addRecorder)

    def where(self, field, operator, value):
        return MockQuery(self._currentSnapshots()).where(field, operator, value)

    def add(self, payload):
        self.addRecorder.append(payload)
        return SimpleNamespace(id=f'status-{len(self.addRecorder)}')


class MockFirestoreClient:
    def __init__(
        self,
        documentSnapshot=None,
        documentSnapshots=None,
        updateRecorder=None,
        addRecorder=None,
    ):
        if documentSnapshots is not None:
            self.documentSnapshots = list(documentSnapshots)
        elif documentSnapshot is not None:
            self.documentSnapshots = [documentSnapshot]
        else:
            self.documentSnapshots = []
        self.updateRecorder = updateRecorder or {'set': None, 'update': []}
        self.addRecorder = addRecorder if addRecorder is not None else []
        self.documentStore = {}
        for snapshot in self.documentSnapshots:
            metadata = snapshot.to_dict()
            if metadata is not None:
                self.documentStore[snapshot.id] = dict(metadata)

    def collection(self, _name):
        snapshots = self.documentSnapshots or self._currentSnapshots()
        return MockCollection(snapshots, self.documentStore, self.updateRecorder, self.addRecorder)

    def transaction(self):
        return MockTransaction(self.documentStore, self.updateRecorder)

    def _currentSnapshots(self):
        return [
            MockDocumentSnapshot(docId, metadata)
            for docId, metadata in self.documentStore.items()
        ]


class MockDocumentSnapshot:
    def __init__(self, docId, metadata, exists=True):
        self.id = docId
        self._metadata = metadata
        self.exists = exists

    def to_dict(self):
        if not self.exists:
            return None
        return self._metadata


@pytest.fixture(autouse=True)
def resetClients(monkeypatch):
    monkeypatch.setattr(main, 'cachedClients', None)
    defaultBundle = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: defaultBundle)
    fakeRequest.files = {}
    fakeRequest.form = {}
    fakeRequest.args = {}
    fakeRequest.headers = {}
    fakeRequest.clear_json()
    fakeRequest.method = 'POST'
    yield


def testUploadFileStoresExpiryMetadata(monkeypatch):
    metadataRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(updateRecorder=metadataRecorder),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'generateFetchToken', lambda: 'testFetchToken')

    fakeRequest.files = {'file': MockUploadFile(b'file-contents', 'test.gcode')}
    fakeRequest.form = {
        'unencrypted_data': json.dumps({'visible': 'info'}),
        'encrypted_data_payload': json.dumps({'secure': 'payload'}),
        'recipient_id': 'recipient123',
        'product_id': '123e4567-e89b-12d3-a456-426614174000',
    }

    responseBody, statusCode = main.uploadFile()

    assert statusCode == 200
    assert responseBody['fetchToken'] == 'testFetchToken'
    storedMetadata = metadataRecorder['set']
    assert storedMetadata is not None
    assert storedMetadata['fetchTokenConsumed'] is False
    assert storedMetadata['fetchTokenExpiry'] > datetime.now(timezone.utc)
    assert storedMetadata['productId'] == '123e4567-e89b-12d3-a456-426614174000'
    assert storedMetadata['lastRequestFileName'] == 'test.gcode'


def testProductHandshakeDownloadFlow(monkeypatch):
    currentTime = datetime.now(timezone.utc)
    metadata = {
        'productId': 'prod-1',
        'fetchToken': 'token-123',
        'fetchTokenConsumed': False,
        'fetchTokenExpiry': currentTime + timedelta(minutes=5),
        'originalFilename': 'part-a.gcode',
        'timestamp': currentTime,
        'unencryptedData': {'visible': 'info'},
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.clear_json()
    fakeRequest.args = {}
    fakeRequest.set_json({'status': 'needsFile'})

    responseBody, statusCode = main.productHandshake('prod-1')

    assert statusCode == 200
    assert responseBody['decision'] == 'full'
    assert responseBody['downloadRequired'] is True
    assert responseBody['fetchToken'] == 'token-123'
    assert responseBody['metadata'] == {'visible': 'info'}
    assert responseBody['originalFilename'] == 'part-a.gcode'
    assert responseBody['lastRequestFileName'] == 'part-a.gcode'
    assert 'lastRequestTimestamp' in responseBody

    assert updateRecorder['update'], 'Expected Firestore handshake update'
    updatePayload = updateRecorder['update'][0]
    assert updatePayload['status'] == 'handshake-download'
    assert updatePayload['handshakeClientStatus'] == 'needsFile'
    assert isinstance(updatePayload['lastRequestTimestamp'], datetime)
    assert updatePayload['lastRequestFileName'] == 'part-a.gcode'


def testProductHandshakeMetadataFlow(monkeypatch):
    earlierTime = datetime.now(timezone.utc) - timedelta(hours=1)
    metadata = {
        'productId': 'prod-2',
        'fetchToken': 'token-456',
        'fetchTokenConsumed': False,
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'originalFilename': 'part-b.gcode',
        'timestamp': datetime.now(timezone.utc),
        'lastRequestTimestamp': earlierTime,
        'unencryptedData': {'preview': 'info'},
    }
    documentSnapshot = MockDocumentSnapshot('doc456', metadata)
    updateRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.clear_json()
    fakeRequest.args = {}
    fakeRequest.set_json({'status': 'hasFile'})

    responseBody, statusCode = main.productHandshake('prod-2')

    assert statusCode == 200
    assert responseBody['decision'] == 'metadata'
    assert responseBody['downloadRequired'] is False
    assert responseBody['metadata'] == {'preview': 'info'}
    assert responseBody['previousRequestTimestamp'] == earlierTime.isoformat()
    assert responseBody['fetchMode'] == 'metadata'

    assert updateRecorder['update'], 'Expected Firestore handshake update'
    updatePayload = updateRecorder['update'][0]
    assert updatePayload['status'] == 'handshake-metadata'
    assert updatePayload['handshakeClientStatus'] == 'hasFile'
    assert isinstance(updatePayload['lastRequestTimestamp'], datetime)
    assert updatePayload['lastRequestFileName'] == 'part-b.gcode'
    assert updatePayload['fetchToken'] is main.DELETE_FIELD
    assert updatePayload['fetchTokenExpiry'] is main.DELETE_FIELD
    assert updatePayload['fetchTokenConsumed'] is True
    assert isinstance(updatePayload['fetchTokenConsumedTimestamp'], datetime)


def testProductStatusUpdateSuccess(monkeypatch):
    currentTime = datetime.now(timezone.utc)
    fetchTokenExpiry = currentTime + timedelta(minutes=10)
    metadata = {
        'productId': 'prod-123',
        'fetchToken': 'token-789',
        'fetchTokenConsumed': False,
        'fetchTokenExpiry': fetchTokenExpiry,
        'timestamp': currentTime,
        'status': 'available',
    }
    documentSnapshot = MockDocumentSnapshot('doc789', metadata)
    statusAddRecorder = []
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, addRecorder=statusAddRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    statusPayload = {
        'productId': 'prod-123',
        'requestedMode': 'full',
        'success': True,
        'fileName': 'print-job.gcode',
        'lastRequestedAt': '2024-01-01T12:00:00Z',
        'recipientId': '  recipient-007  ',
        'printerDetails': {
            'serialNumber': 'SN-001',
            'ipAddress': '192.168.1.10',
            'nickname': 'Workhorse',
            'brand': 'Prusa',
        },
        'printerEvent': {
            'eventType': 'job-complete',
            'message': 'Completed',
        },
    }
    fakeRequest.set_json(statusPayload)

    responseBody, statusCode = main.productStatusUpdate('prod-123')

    assert statusCode == 200
    assert responseBody == {'message': 'Product status recorded'}
    assert len(statusAddRecorder) == 1
    storedStatus = statusAddRecorder[0]
    assert storedStatus['productId'] == 'prod-123'
    assert storedStatus['fileId'] == 'doc789'
    assert storedStatus['requestedMode'] == 'full'
    assert storedStatus['success'] is True
    assert storedStatus['fileName'] == 'print-job.gcode'
    assert storedStatus['lastRequestedAt'] == '2024-01-01T12:00:00+00:00'
    assert storedStatus['payload'] == statusPayload
    fetchTokenData = storedStatus['fetchTokenData']
    assert fetchTokenData['fetchToken'] == 'token-789'
    assert fetchTokenData['fetchTokenConsumed'] is False
    assert fetchTokenData['fetchTokenExpiry'] == fetchTokenExpiry.isoformat()
    assert storedStatus['fileStatus'] == 'available'
    assert storedStatus['fileTimestamp'] == currentTime.isoformat()
    assert storedStatus['printerSerial'] == 'SN-001'
    assert storedStatus['printerIpAddress'] == '192.168.1.10'
    assert storedStatus['printerNickname'] == 'Workhorse'
    assert storedStatus['printerBrand'] == 'Prusa'
    assert storedStatus['statusEvent'] == 'job-complete'
    assert storedStatus['statusMessage'] == 'Completed'
    assert storedStatus['recipientId'] == 'recipient-007'


def testProductStatusUpdateIgnoresNonStringRecipientId(monkeypatch):
    currentTime = datetime.now(timezone.utc)
    metadata = {
        'productId': 'prod-123',
        'fetchToken': 'token-xyz',
        'fetchTokenConsumed': False,
        'timestamp': currentTime,
    }
    documentSnapshot = MockDocumentSnapshot('docABC', metadata)
    statusAddRecorder = []
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, addRecorder=statusAddRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    statusPayload = {
        'productId': 'prod-123',
        'requestedMode': 'metadata',
        'success': False,
        'fileName': 'print-job.gcode',
        'lastRequestedAt': '2024-01-02T12:00:00Z',
        'recipientId': 987,
    }
    fakeRequest.set_json(statusPayload)

    responseBody, statusCode = main.productStatusUpdate('prod-123')

    assert statusCode == 200
    assert responseBody == {'message': 'Product status recorded'}
    assert len(statusAddRecorder) == 1
    storedStatus = statusAddRecorder[0]
    assert 'recipientId' not in storedStatus


def testProductStatusUpdateUsesLatestPrinterEventFromList(monkeypatch):
    currentTime = datetime.now(timezone.utc)
    metadata = {
        'productId': 'prod-123',
        'fetchToken': 'token-111',
        'fetchTokenConsumed': False,
        'timestamp': currentTime,
    }
    documentSnapshot = MockDocumentSnapshot('doc999', metadata)
    statusAddRecorder = []
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, addRecorder=statusAddRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    statusPayload = {
        'productId': 'prod-123',
        'requestedMode': 'metadata',
        'success': True,
        'fileName': 'print-job.gcode',
        'lastRequestedAt': '2024-01-03T12:00:00Z',
        'printerDetails': [
            {'serialNumber': 'SN-OLD', 'ipAddress': '192.168.1.9'},
            {
                'serialNumber': 'SN-NEW',
                'ipAddress': '192.168.1.11',
                'nickname': 'Queue',
                'brand': 'Bambu',
            },
        ],
        'printerEvent': [
            {'event': 'queued', 'message': 'Queued'},
            {'status': 'printing', 'detail': 'Working'},
            {'type': 'job-complete', 'description': 'Finished printing'},
        ],
    }
    fakeRequest.set_json(statusPayload)

    responseBody, statusCode = main.productStatusUpdate('prod-123')

    assert statusCode == 200
    assert responseBody == {'message': 'Product status recorded'}
    assert len(statusAddRecorder) == 1
    storedStatus = statusAddRecorder[0]
    assert storedStatus['printerSerial'] == 'SN-NEW'
    assert storedStatus['printerIpAddress'] == '192.168.1.11'
    assert storedStatus['printerNickname'] == 'Queue'
    assert storedStatus['printerBrand'] == 'Bambu'
    assert storedStatus['statusEvent'] == 'job-complete'
    assert storedStatus['statusMessage'] == 'Finished printing'


def testProductStatusUpdateIgnoresNonMappingPrinterDetails(monkeypatch):
    currentTime = datetime.now(timezone.utc)
    metadata = {
        'productId': 'prod-123',
        'fetchToken': None,
        'fetchTokenConsumed': False,
        'timestamp': currentTime,
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    statusAddRecorder = []
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, addRecorder=statusAddRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    statusPayload = {
        'productId': 'prod-123',
        'requestedMode': 'metadata',
        'success': False,
        'fileName': 'print-job.gcode',
        'lastRequestedAt': '2024-01-02T12:00:00Z',
        'printerDetails': 'unexpected',
        'printerEvent': 'nope',
    }
    fakeRequest.set_json(statusPayload)

    responseBody, statusCode = main.productStatusUpdate('prod-123')

    assert statusCode == 200
    assert responseBody == {'message': 'Product status recorded'}
    assert len(statusAddRecorder) == 1
    storedStatus = statusAddRecorder[0]
    assert 'printerSerial' not in storedStatus
    assert 'printerIpAddress' not in storedStatus
    assert 'printerNickname' not in storedStatus
    assert 'printerBrand' not in storedStatus
    assert 'statusEvent' not in storedStatus
    assert 'statusMessage' not in storedStatus


def testProductStatusUpdateMissingFields(monkeypatch):
    statusAddRecorder = []
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(addRecorder=statusAddRecorder),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    incompletePayload = {
        'productId': 'prod-123',
        'success': True,
        'fileName': 'missing-fields.gcode',
    }
    fakeRequest.set_json(incompletePayload)

    responseBody, statusCode = main.productStatusUpdate('prod-123')

    assert statusCode == 400
    assert 'error' in responseBody
    assert statusAddRecorder == []


def testProductStatusUpdateInvalidJson(monkeypatch):
    statusAddRecorder = []
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(addRecorder=statusAddRecorder),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.is_json = True

    def raiseJsonError():
        raise ValueError('invalid json')

    monkeypatch.setattr(fakeRequest, 'get_json', raiseJsonError)

    responseBody, statusCode = main.productStatusUpdate('prod-123')

    assert statusCode == 400
    assert responseBody == {'error': 'Invalid JSON payload'}
    assert statusAddRecorder == []

def testFetchFileFirstUseSuccess(monkeypatch):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
        'originalFilename': None,
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.files = {}
    fakeRequest.form = {}
    fakeRequest.args = {}
    fakeRequest.clear_json()

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 200
    assert responseBody['decryptedData'] == {'sensitive': 'value'}
    assert responseBody['unencryptedData'] == {'visible': 'info'}
    assert responseBody['fetchMode'] == 'full'
    assert 'signedUrl' in responseBody
    assert 'lastRequestTimestamp' in responseBody
    assert responseBody['lastRequestFileName'] is None

    assert updateRecorder['update'], 'Expected Firestore update to be recorded'
    updatePayload = updateRecorder['update'][0]
    assert updatePayload['fetchToken'] is main.DELETE_FIELD
    assert updatePayload['fetchTokenExpiry'] is main.DELETE_FIELD
    assert updatePayload['fetchTokenConsumed'] is True
    assert updatePayload['status'] == 'fetched'
    assert updatePayload['lastFetchMode'] == 'full'
    assert isinstance(updatePayload['lastRequestTimestamp'], datetime)
    assert updatePayload['lastRequestFileName'] is None


def testFetchFileMetadataOnly(monkeypatch):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
        'originalFilename': 'part-c.gcode',
    }
    documentSnapshot = MockDocumentSnapshot('doc789', metadata)
    updateRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.files = {}
    fakeRequest.form = {}
    fakeRequest.args = {'mode': 'metadata'}
    fakeRequest.clear_json()

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 200
    assert responseBody['fetchMode'] == 'metadata'
    assert responseBody['message'] == 'Metadata retrieved successfully'
    assert 'signedUrl' not in responseBody
    assert responseBody['lastRequestFileName'] == 'part-c.gcode'

    assert updateRecorder['update'], 'Expected Firestore update to be recorded'
    updatePayload = updateRecorder['update'][0]
    assert updatePayload['status'] == 'metadata-fetched'
    assert updatePayload['lastFetchMode'] == 'metadata'
    assert isinstance(updatePayload['lastRequestTimestamp'], datetime)
    assert updatePayload['lastRequestFileName'] == 'part-c.gcode'
    assert updatePayload['fetchToken'] is main.DELETE_FIELD
    assert updatePayload['fetchTokenExpiry'] is main.DELETE_FIELD
    assert updatePayload['fetchTokenConsumed'] is True
    assert (
        updatePayload['fetchTokenConsumedTimestamp']
        is main.firestore.SERVER_TIMESTAMP
    )
    assert (
        updatePayload['metadataFetchTimestamp'] is main.firestore.SERVER_TIMESTAMP
    )

def testFetchFileUsesIamSigningWhenSignBytesMissing(monkeypatch):
    metadata = {
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}

    generateSignedUrlCalls = []

    class IamFallbackBlob(MockBlob):
        def generate_signed_url(self, **kwargs):
            generateSignedUrlCalls.append(kwargs)
            return 'https://example.com/iam-signed-url'

    class IamFallbackBucket(MockBucket):
        def blob(self, _name):
            return IamFallbackBlob()

    class IamFallbackCredentials:
        def __init__(self):
            self.token = None
            self.service_account_email = 'service@example.iam.gserviceaccount.com'

        def refresh(self, _requestAdapter):
            self.token = 'new-access-token'

    class IamFallbackStorageClient(MockStorageClient):
        def __init__(self):
            self._credentials = IamFallbackCredentials()

        def bucket(self, _name):
            return IamFallbackBucket()

    mockClients = main.ClientBundle(
        storageClient=IamFallbackStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'Request', lambda: SimpleNamespace())

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 200
    assert responseBody['signedUrl'] == 'https://example.com/iam-signed-url'
    assert generateSignedUrlCalls
    kwargs = generateSignedUrlCalls[0]
    assert kwargs['service_account_email'] == 'service@example.iam.gserviceaccount.com'
    assert kwargs['access_token'] == 'new-access-token'
    assert kwargs['method'] == 'GET'
    assert kwargs['version'] == 'v4'
    assert updateRecorder['update'], 'Expected Firestore update to be recorded'


def testFetchFileScopesCredentialsForIamSigning(monkeypatch):
    metadata = {
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}

    generateSignedUrlCalls = []

    class ScopedBlob(MockBlob):
        def generate_signed_url(self, **kwargs):
            generateSignedUrlCalls.append(kwargs)
            return 'https://example.com/scoped-iam-url'

    class ScopedBucket(MockBucket):
        def blob(self, _name):
            return ScopedBlob()

    class ScopedCredential:
        def __init__(self, scopes):
            self.scopes = scopes
            self.token = None
            self.service_account_email = 'scoped@example.iam.gserviceaccount.com'
            self.refreshCalls = 0

        def refresh(self, _requestAdapter):
            self.refreshCalls += 1
            self.token = 'scoped-access-token'

    class CredentialRequiringScopes:
        def __init__(self):
            self.withScopesCalledWith = None
            self.lastScopedCredential = None

        def with_scopes_if_required(self, scopes):
            self.withScopesCalledWith = scopes
            self.lastScopedCredential = ScopedCredential(scopes)
            return self.lastScopedCredential

    class ScopedStorageClient(MockStorageClient):
        def __init__(self):
            self._credentials = CredentialRequiringScopes()

        def bucket(self, _name):
            return ScopedBucket()

    scopedStorageClient = ScopedStorageClient()

    mockClients = main.ClientBundle(
        storageClient=scopedStorageClient,
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'Request', lambda: SimpleNamespace())

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 200
    assert responseBody['signedUrl'] == 'https://example.com/scoped-iam-url'
    assert generateSignedUrlCalls, 'Expected IAM signed URL to be generated'

    credentials = scopedStorageClient._credentials  # pylint: disable=protected-access
    assert credentials.withScopesCalledWith == ['https://www.googleapis.com/auth/cloud-platform']

    scopedCredential = credentials.lastScopedCredential
    assert scopedCredential.refreshCalls == 1

    kwargs = generateSignedUrlCalls[0]
    assert kwargs['access_token'] == 'scoped-access-token'
    assert (
        kwargs['service_account_email']
        == 'scoped@example.iam.gserviceaccount.com'
    )

    assert updateRecorder['update'], 'Expected Firestore update to be recorded'


def testFetchFileInvalidDecryptedMetadata(monkeypatch):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)

    class InvalidJsonKmsClient(MockEncryptClient):
        def decrypt(self, **_kwargs):
            return SimpleNamespace(plaintext=b'invalid json')

    updateRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(documentSnapshot=documentSnapshot, updateRecorder=updateRecorder),
        kmsClient=InvalidJsonKmsClient(),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 422
    assert responseBody == {'error': 'Decrypted metadata is invalid JSON'}


def testFetchFileMissingIamPermissions(monkeypatch):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}

    class ForbiddenBlob(MockBlob):
        def generate_signed_url(self, **_kwargs):
            raise main.Forbidden('caller does not have storage.objects.sign access')

    class ForbiddenBucket(MockBucket):
        def blob(self, _name):
            return ForbiddenBlob()

    class ForbiddenStorageClient(MockStorageClient):
        def bucket(self, _name):
            return ForbiddenBucket()

    mockClients = main.ClientBundle(
        storageClient=ForbiddenStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 403
    assert responseBody['error'].startswith('Missing required IAM permissions')
    assert responseBody['missingPermissions'] == [
        'storage.objects.sign',
        'iam.serviceAccounts.signBlob',
    ]
    assert 'storage.objects.sign' in responseBody['detail']
    assert updateRecorder['update'] == []


def testFetchFileStorageApiError(monkeypatch, caplog):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}

    class StorageErrorBlob(MockBlob):
        def generate_signed_url(self, **_kwargs):
            raise main.GoogleAPICallError('transient backend failure')

    class StorageErrorBucket(MockBucket):
        def blob(self, _name):
            return StorageErrorBlob()

    class StorageErrorClient(MockStorageClient):
        def bucket(self, _name):
            return StorageErrorBucket()

    mockClients = main.ClientBundle(
        storageClient=StorageErrorClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    caplog.set_level(logging.ERROR)

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 503
    assert responseBody == {
        'error': 'Storage service temporarily unavailable for signed URL generation',
        'detail': 'transient backend failure',
    }
    assert updateRecorder['update'] == []
    assert any(
        'Storage API call failed during signed URL generation' in record.getMessage()
        for record in caplog.records
    )


def testFetchFileMissingSigningKey(monkeypatch):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}

    class MissingSigningKeyBlob(MockBlob):
        def generate_signed_url(self, **_kwargs):
            raise AttributeError('Credentials lack signing key')

    class MissingSigningKeyBucket(MockBucket):
        def blob(self, _name):
            return MissingSigningKeyBlob()

    class MissingSigningKeyStorageClient(MockStorageClient):
        def bucket(self, _name):
            return MissingSigningKeyBucket()

    mockClients = main.ClientBundle(
        storageClient=MissingSigningKeyStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 503
    assert responseBody == {
        'error': 'Service account lacks a signing capability required for signed URL generation',
        'detail': 'Credentials lack signing key',
    }


def testFetchFileUnsignedCredentialsLogsDetail(monkeypatch, caplog):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}

    class UnsignedCredentialsBlob(MockBlob):
        def generate_signed_url(self, **_kwargs):
            raise AttributeError('Credentials are unable to sign blobs')

    class UnsignedCredentialsBucket(MockBucket):
        def blob(self, _name):
            return UnsignedCredentialsBlob()

    class UnsignedCredentialsStorageClient(MockStorageClient):
        def bucket(self, _name):
            return UnsignedCredentialsBucket()

    mockClients = main.ClientBundle(
        storageClient=UnsignedCredentialsStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    caplog.set_level(logging.ERROR)

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 503
    assert responseBody == {
        'error': 'Service account lacks a signing capability required for signed URL generation',
        'detail': 'Credentials are unable to sign blobs',
    }
    assert updateRecorder['update'] == []
    assert any(
        'missing a signing key required for signed URL generation' in record.getMessage()
        for record in caplog.records
    )


def testFetchFileReturnsLegacyUnencryptedMetadata(monkeypatch):
    metadata = {
        'unencryptedData': {'legacy': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}

    class SpyKmsClient(MockEncryptClient):
        def __init__(self):
            super().__init__()
            self.decryptCalled = False

        def decrypt(self, **kwargs):  # pylint: disable=unused-argument
            self.decryptCalled = True
            return super().decrypt(**kwargs)

    spyKmsClient = SpyKmsClient()
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=spyKmsClient,
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.files = {}
    fakeRequest.form = {}

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 200
    assert responseBody['decryptedData'] == {'legacy': 'info'}
    assert responseBody['unencryptedData'] == {'legacy': 'info'}
    assert spyKmsClient.decryptCalled is False

    assert updateRecorder['update'], 'Expected Firestore update to be recorded'
    updatePayload = updateRecorder['update'][0]
    assert updatePayload['fetchTokenConsumed'] is True


def testFetchFileMissingGcsPath(monkeypatch):
    metadata = {
        'unencryptedData': {'visible': 'info'},
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 422
    assert responseBody == {'error': 'File metadata is incomplete: missing gcsPath'}
    assert updateRecorder['update'] == []


def testFetchFileRejectsConsumedToken(monkeypatch):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': True,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.files = {}
    fakeRequest.form = {}

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 410
    assert 'already used' in responseBody['error']
    assert not updateRecorder['update'], 'Update should not be called for consumed tokens'


def testFetchFileRejectsExpiredToken(monkeypatch):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) - timedelta(minutes=1),
        'fetchTokenConsumed': False,
        'fetchToken': 'testFetchToken',
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.files = {}
    fakeRequest.form = {}

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 410
    assert 'expired' in responseBody['error']
    assert not updateRecorder['update'], 'Update should not be called for expired tokens'


def testListPendingJobsReturnsActiveEntries(monkeypatch):
    activeMetadata = {
        'originalFilename': 'file.gcode',
        'productId': 'product-active',
        'fetchToken': 'token-active',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'status': 'uploaded',
        'timestamp': datetime.now(timezone.utc),
        'recipientId': 'recipient123',
    }
    expiredMetadata = {
        'originalFilename': 'old-file.gcode',
        'productId': 'product-expired',
        'fetchToken': 'token-expired',
        'fetchTokenExpiry': datetime.now(timezone.utc) - timedelta(minutes=1),
        'fetchTokenConsumed': False,
        'status': 'uploaded',
        'timestamp': datetime.now(timezone.utc),
        'recipientId': 'recipient123',
    }
    documentSnapshots = [
        MockDocumentSnapshot('doc-active', activeMetadata),
        MockDocumentSnapshot('doc-expired', expiredMetadata),
    ]
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(documentSnapshots=documentSnapshots),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.headers = {}
    fakeRequest.args = {}
    fakeRequest.set_json({'recipientId': 'recipient123'})

    responseBody, statusCode = main.listPendingJobs('app-123')

    assert statusCode == 200
    assert responseBody['ok'] is True
    assert responseBody['recipientId'] == 'recipient123'
    pendingFiles = responseBody['pending']
    assert len(pendingFiles) == 1
    assert pendingFiles[0]['fileId'] == 'doc-active'
    assert pendingFiles[0]['fetchToken'] == 'token-active'
    assert pendingFiles[0]['productId'] == 'product-active'
    assert responseBody['skipped'] == ['doc-expired']


def testListPendingJobsSkipsJobsNotReadyForClaim(monkeypatch):
    readyMetadata = {
        'originalFilename': 'file.gcode',
        'productId': 'product-ready',
        'fetchToken': 'token-ready',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'status': 'queued',
        'timestamp': datetime.now(timezone.utc),
        'recipientId': 'recipient123',
    }
    printingMetadata = {
        'originalFilename': 'file-printing.gcode',
        'productId': 'product-printing',
        'fetchToken': 'token-printing',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
        'status': 'printing',
        'timestamp': datetime.now(timezone.utc),
        'recipientId': 'recipient123',
    }
    documentSnapshots = [
        MockDocumentSnapshot('doc-ready', readyMetadata),
        MockDocumentSnapshot('doc-printing', printingMetadata),
    ]
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(documentSnapshots=documentSnapshots),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.headers = {}
    fakeRequest.args = {}
    fakeRequest.set_json({'recipientId': 'recipient123'})

    responseBody, statusCode = main.listPendingJobs('app-123')

    assert statusCode == 200
    assert responseBody['ok'] is True
    assert [item['fileId'] for item in responseBody['pending']] == ['doc-ready']


def testListRecipientFilesAliasCallsListPendingJobs(monkeypatch):
    pendingResponse = {'ok': True, 'pending': []}
    monkeypatch.setattr(main, 'listPendingJobs', lambda appId: (pendingResponse, 200))

    responseBody, statusCode = main.listRecipientFilesAlias('app-xyz')

    assert statusCode == 200
    assert responseBody == pendingResponse


def testClaimJobUpdatesStatusToPrinting(monkeypatch):
    updateRecorder = {'set': None, 'update': []}
    jobMetadata = {
        'recipientId': 'recipient123',
        'status': 'uploaded',
        'fetchTokenConsumed': False,
        'fetchToken': 'token-ready',
    }
    documentSnapshot = MockDocumentSnapshot('job-123', jobMetadata)
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=documentSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.headers = {}
    fakeRequest.args = {}
    fakeRequest.set_json(
        {
            'jobId': 'job-123',
            'printerId': 'printer-1',
            'recipientId': 'recipient123',
        }
    )

    responseBody, statusCode = main.claimJob('app-123')

    assert statusCode == 200
    assert responseBody['ok'] is True
    assert responseBody['jobId'] == 'job-123'
    assert responseBody['assignedPrinterId'] == 'printer-1'
    assert responseBody['status'] == 'printing'
    assert mockClients.firestoreClient.documentStore['job-123']['status'] == 'printing'

    assert updateRecorder['update'], 'Expected update to be recorded'
    updatePayload = updateRecorder['update'][-1]
    assert updatePayload['status'] == 'printing'
    assert updatePayload['assignedPrinterId'] == 'printer-1'
    assert updatePayload['claimedBy'] == 'recipient123'
    assert updatePayload['claimedAt'] is firestoreModule.SERVER_TIMESTAMP


def testClaimJobRejectsWhenAlreadyClaimed(monkeypatch):
    jobMetadata = {
        'recipientId': 'recipient123',
        'status': 'printing',
        'assignedPrinterId': 'printer-existing',
    }
    documentSnapshot = MockDocumentSnapshot('job-123', jobMetadata)
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(documentSnapshot=documentSnapshot),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.headers = {}
    fakeRequest.args = {}
    fakeRequest.set_json(
        {
            'jobId': 'job-123',
            'printerId': 'printer-new',
            'recipientId': 'recipient123',
        }
    )

    responseBody, statusCode = main.claimJob('app-123')

    assert statusCode == 409
    assert responseBody['ok'] is False
    assert 'already' in responseBody['message'].lower()


def testClaimJobReturnsNotFoundForMissingJob(monkeypatch):
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    fakeRequest.headers = {}
    fakeRequest.args = {}
    fakeRequest.set_json(
        {
            'jobId': 'job-missing',
            'printerId': 'printer-1',
            'recipientId': 'recipient123',
        }
    )

    responseBody, statusCode = main.claimJob('app-123')

    assert statusCode == 404
    assert responseBody['ok'] is False
    assert responseBody['error_type'] == 'NotFound'


def testPrinterStatusUpdateStoresRecipientId(monkeypatch):
    addRecorder = []
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(addRecorder=addRecorder),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'test-key'})

    fakeRequest.headers = {'X-API-Key': 'test-key'}
    fakeRequest.set_json(
        {
            'printerIpAddress': '192.168.1.10',
            'publicKey': 'public',
            'accessCode': 'access',
            'printerSerial': 'printer-1',
            'objectName': 'object',
            'useAms': True,
            'printJobId': 'job-1',
            'productName': 'product',
            'platesRequested': 1,
            'status': 'printing',
            'jobProgress': 50,
            'materialLevel': {'filamentA': 10},
            'recipientId': ' recipient-abc ',
        }
    )

    responseBody, statusCode = main.printerStatusUpdate()

    assert statusCode == 200
    assert responseBody['ok'] is True
    assert responseBody['success'] is True
    assert responseBody['message'] == 'Printer status updated successfully'
    assert responseBody['statusId'] == 'status-1'
    assert len(addRecorder) == 1
    storedPayload = addRecorder[0]
    assert storedPayload['recipientId'] == 'recipient-abc'
    assert 'printerSerial' not in storedPayload
    assert 'accessCode' not in storedPayload


def testLoadPrinterApiKeysFromEnvironment(monkeypatch):
    monkeypatch.setenv('API_KEYS_PRINTER_STATUS', 'alpha , beta,, gamma ')
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS_PATH', raising=False)
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS', raising=False)

    apiKeys = main.loadPrinterApiKeys()

    assert apiKeys == {'alpha', 'beta', 'gamma'}


def testLoadPrinterApiKeysFromAliasInlineValue(monkeypatch):
    monkeypatch.delenv('API_KEYS_PRINTER_STATUS', raising=False)
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS_PATH', raising=False)
    monkeypatch.setenv('SECRET_MANAGER_API_KEYS', 'inline-alpha\ninline-beta')

    apiKeys = main.loadPrinterApiKeys()

    assert apiKeys == {'inline-alpha', 'inline-beta'}


def testLoadPrinterApiKeysFromSecretManager(monkeypatch):
    monkeypatch.delenv('API_KEYS_PRINTER_STATUS', raising=False)
    secretPath = 'projects/test/secrets/printer-keys/versions/latest'
    monkeypatch.setenv('SECRET_MANAGER_API_KEYS_PATH', secretPath)
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS', raising=False)

    class FakeSecretManagerClient:
        def __init__(self):
            self.requestedNames = []

        def access_secret_version(self, name):
            self.requestedNames.append(name)
            return SimpleNamespace(payload=SimpleNamespace(data=b'key-one, key-two'))

    fakeClient = FakeSecretManagerClient()
    monkeypatch.setattr(
        main.secretmanager, 'SecretManagerServiceClient', lambda: fakeClient
    )

    apiKeys = main.loadPrinterApiKeys()

    assert apiKeys == {'key-one', 'key-two'}
    assert fakeClient.requestedNames == [secretPath]


def testLoadPrinterApiKeysFromAliasSecretPath(monkeypatch):
    monkeypatch.delenv('API_KEYS_PRINTER_STATUS', raising=False)
    secretPath = 'projects/test/secrets/printer-keys/versions/latest'
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS_PATH', raising=False)
    monkeypatch.setenv('SECRET_MANAGER_API_KEYS', secretPath)

    class FakeSecretManagerClient:
        def __init__(self):
            self.requestedNames = []

        def access_secret_version(self, name):
            self.requestedNames.append(name)
            return SimpleNamespace(payload=SimpleNamespace(data=b'alias-one,alias-two'))

    fakeClient = FakeSecretManagerClient()
    monkeypatch.setattr(
        main.secretmanager, 'SecretManagerServiceClient', lambda: fakeClient
    )

    apiKeys = main.loadPrinterApiKeys()

    assert apiKeys == {'alias-one', 'alias-two'}
    assert fakeClient.requestedNames == [secretPath]


def testLoadPrinterApiKeysFromSecretManagerWithNewlines(monkeypatch):
    monkeypatch.delenv('API_KEYS_PRINTER_STATUS', raising=False)
    secretPath = 'projects/test/secrets/printer-keys/versions/latest'
    monkeypatch.setenv('SECRET_MANAGER_API_KEYS_PATH', secretPath)
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS', raising=False)

    class FakeSecretManagerClient:
        def __init__(self):
            self.requestedNames = []

        def access_secret_version(self, name):
            self.requestedNames.append(name)
            secretPayload = 'first-key\nsecond-key\nthird-key'
            return SimpleNamespace(
                payload=SimpleNamespace(data=secretPayload.encode('utf-8'))
            )

    fakeClient = FakeSecretManagerClient()
    monkeypatch.setattr(
        main.secretmanager, 'SecretManagerServiceClient', lambda: fakeClient
    )

    apiKeys = main.loadPrinterApiKeys()

    assert apiKeys == {'first-key', 'second-key', 'third-key'}
    assert fakeClient.requestedNames == [secretPath]


def testLoadPrinterApiKeysTreatsEachLineAsSeparateKey(monkeypatch):
    monkeypatch.delenv('API_KEYS_PRINTER_STATUS', raising=False)
    secretPath = 'projects/test/secrets/printer-keys/versions/latest'
    monkeypatch.setenv('SECRET_MANAGER_API_KEYS_PATH', secretPath)
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS', raising=False)

    class FakeSecretManagerClient:
        def __init__(self):
            self.requestedNames = []

        def access_secret_version(self, name):
            self.requestedNames.append(name)
            secretPayload = 'line-key-one\nline-key-two\nline-key-three'
            return SimpleNamespace(
                payload=SimpleNamespace(data=secretPayload.encode('utf-8'))
            )

    fakeClient = FakeSecretManagerClient()
    monkeypatch.setattr(
        main.secretmanager, 'SecretManagerServiceClient', lambda: fakeClient
    )

    apiKeys = main.loadPrinterApiKeys()

    assert apiKeys == {'line-key-one', 'line-key-two', 'line-key-three'}
    assert fakeClient.requestedNames == [secretPath]


def testLoadPrinterApiKeysHandlesNewlineSeparatedEntries(monkeypatch):
    monkeypatch.delenv('API_KEYS_PRINTER_STATUS', raising=False)
    secretPath = 'projects/test/secrets/printer-keys/versions/latest'
    monkeypatch.setenv('SECRET_MANAGER_API_KEYS_PATH', secretPath)
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS', raising=False)

    class FakeSecretManagerClient:
        def __init__(self):
            self.requestedNames = []

        def access_secret_version(self, name):
            self.requestedNames.append(name)
            secretPayload = 'first-key\nsecond-key\r\nthird-key,\n\n'
            return SimpleNamespace(
                payload=SimpleNamespace(data=secretPayload.encode('utf-8'))
            )

    fakeClient = FakeSecretManagerClient()
    monkeypatch.setattr(
        main.secretmanager, 'SecretManagerServiceClient', lambda: fakeClient
    )

    apiKeys = main.loadPrinterApiKeys()

    assert apiKeys == {'first-key', 'second-key', 'third-key'}
    assert fakeClient.requestedNames == [secretPath]


def testLoadPrinterApiKeysFromSecretManagerReadmeExample(monkeypatch):
    monkeypatch.delenv('API_KEYS_PRINTER_STATUS', raising=False)
    secretPath = 'projects/test/secrets/printer-keys/versions/latest'
    monkeypatch.setenv('SECRET_MANAGER_API_KEYS_PATH', secretPath)
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS', raising=False)

    readmeSecret = (
        '1ORJkv4IZtQjYIniGFX8fr340VreiBhK1XNcDZ3GVlaNSPSCkm6EIZy4m6XOJDF0XAPLcELuZSQnEHxvBMqhD9b5q5Klf0QE9fwih9TOgC2K643cOrhOPZJMVwb9BV7i\n'
        '5Q7R8u8mxPutdWz0RVXP7w\n'
        '\n'
        'c3Lr1YyProjUnzf2GeG8MeGYb0UWNt5jnZLd6Svk7DvysymtwkcJatQC4xlsdK9Cy3h4nFkEJmAXBib99tE5N7Ake2OO7rzZGhQSnGcXjhcYu1YOd7rwLKkHecqU8m4b\n'
        'FBjY9CBztbFRsRT883DFi7\n'
    )

    class FakeSecretManagerClient:
        def __init__(self):
            self.requestedNames = []

        def access_secret_version(self, name):
            self.requestedNames.append(name)
            return SimpleNamespace(
                payload=SimpleNamespace(data=readmeSecret.encode('utf-8'))
            )

    fakeClient = FakeSecretManagerClient()
    monkeypatch.setattr(
        main.secretmanager, 'SecretManagerServiceClient', lambda: fakeClient
    )

    apiKeys = main.loadPrinterApiKeys()

    expectedKeys = {
        '1ORJkv4IZtQjYIniGFX8fr340VreiBhK1XNcDZ3GVlaNSPSCkm6EIZy4m6XOJDF0XAPLcELuZSQnEHxvBMqhD9b5q5Klf0QE9fwih9TOgC2K643cOrhOPZJMVwb9BV7i',
        '5Q7R8u8mxPutdWz0RVXP7w',
        'c3Lr1YyProjUnzf2GeG8MeGYb0UWNt5jnZLd6Svk7DvysymtwkcJatQC4xlsdK9Cy3h4nFkEJmAXBib99tE5N7Ake2OO7rzZGhQSnGcXjhcYu1YOd7rwLKkHecqU8m4b',
        'FBjY9CBztbFRsRT883DFi7',
    }

    assert apiKeys == expectedKeys
    assert fakeClient.requestedNames == [secretPath]


def testLoadPrinterApiKeysWithoutConfigurationLogsWarning(monkeypatch, caplog):
    monkeypatch.delenv('API_KEYS_PRINTER_STATUS', raising=False)
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS_PATH', raising=False)
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS', raising=False)

    with caplog.at_level(logging.WARNING):
        apiKeys = main.loadPrinterApiKeys()

    assert apiKeys == set()
    assert (
        'Printer API keys are not configured. Set API_KEYS_PRINTER_STATUS, SECRET_MANAGER_API_KEYS_PATH, or SECRET_MANAGER_API_KEYS.'
        in caplog.text
    )


def testPrinterStatusUpdateAcceptsKeyFromHelper(monkeypatch):
    addRecorder = []
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(addRecorder=addRecorder),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    monkeypatch.setenv('API_KEYS_PRINTER_STATUS', 'refreshed-key')
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS_PATH', raising=False)
    loadedKeys = main.loadPrinterApiKeys()
    monkeypatch.setattr(main, 'validPrinterApiKeys', loadedKeys)

    fakeRequest.headers = {'X-API-Key': 'refreshed-key'}
    fakeRequest.set_json(
        {
            'printerIpAddress': '192.168.1.10',
            'publicKey': 'public',
            'accessCode': 'access',
            'printerSerial': 'printer-1',
            'objectName': 'object',
            'useAms': True,
            'printJobId': 'job-1',
            'productName': 'product',
            'platesRequested': 1,
            'status': 'printing',
            'jobProgress': 50,
            'materialLevel': {'filamentA': 10},
            'recipientId': ' recipient-abc ',
        }
    )

    responseBody, statusCode = main.printerStatusUpdate()

    assert statusCode == 200
    assert responseBody['ok'] is True
    assert responseBody['success'] is True
    assert responseBody['message'] == 'Printer status updated successfully'
    assert len(addRecorder) == 1


def testPrinterStatusUpdateAcceptsInlineSecretManagerKeys(monkeypatch):
    addRecorder = []
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(addRecorder=addRecorder),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)

    monkeypatch.delenv('API_KEYS_PRINTER_STATUS', raising=False)
    monkeypatch.setenv('SECRET_MANAGER_API_KEYS_PATH', 'inline-one\ninline-two')
    monkeypatch.delenv('SECRET_MANAGER_API_KEYS', raising=False)
    loadedKeys = main.loadPrinterApiKeys()
    assert loadedKeys == {'inline-one', 'inline-two'}
    monkeypatch.setattr(main, 'validPrinterApiKeys', loadedKeys)

    fakeRequest.headers = {'X-API-Key': 'inline-two'}
    fakeRequest.set_json(
        {
            'printerIpAddress': '192.168.1.10',
            'publicKey': 'public',
            'accessCode': 'access',
            'printerSerial': 'printer-1',
            'objectName': 'object',
            'useAms': True,
            'printJobId': 'job-1',
            'productName': 'product',
            'platesRequested': 1,
            'status': 'printing',
            'jobProgress': 50,
            'materialLevel': {'filamentA': 10},
        }
    )

    responseBody, statusCode = main.printerStatusUpdate()

    assert statusCode == 200
    assert responseBody['ok'] is True
    assert responseBody['success'] is True
    assert responseBody['message'] == 'Printer status updated successfully'
    assert responseBody['statusId'] == 'status-1'
    assert len(addRecorder) == 1


def testPrinterStatusUpdateRejectsInvalidRecipientId(monkeypatch):
    addRecorder = []
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(addRecorder=addRecorder),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )
    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'test-key'})

    fakeRequest.headers = {'X-API-Key': 'test-key'}
    fakeRequest.set_json(
        {
            'printerIpAddress': '192.168.1.10',
            'publicKey': 'public',
            'accessCode': 'access',
            'printerSerial': 'printer-1',
            'objectName': 'object',
            'useAms': True,
            'printJobId': 'job-1',
            'productName': 'product',
            'platesRequested': 1,
            'status': 'printing',
            'jobProgress': 50,
            'materialLevel': {'filamentA': 10},
            'recipientId': 123,
        }
    )

    responseBody, statusCode = main.printerStatusUpdate()

    assert statusCode == 400
    assert responseBody['ok'] is False
    assert responseBody['error_type'] == 'ValidationError'
    assert responseBody['message'] == 'recipientId must be a non-empty string'
    assert not addRecorder


def testQueuePrinterControlCommandStoresRecord(monkeypatch):
    updateRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(updateRecorder=updateRecorder),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.set_json(
        {
            'commandType': 'set_nozzle_temp',
            'printerIpAddress': '192.168.1.5',
            'recipientId': 'recipient-123',
            'metadata': {'target': 215},
        }
    )

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 202
    assert responseBody['ok'] is True
    assert responseBody['status'] == 'queued'
    assert updateRecorder['set'] is not None
    assert updateRecorder['set']['commandType'] == 'set_nozzle_temp'
    assert updateRecorder['set']['status'] == 'pending'
    assert updateRecorder['set']['metadata'] == {'target': 215}
    assert updateRecorder['set']['recipientId'] == 'recipient-123'
    assert updateRecorder['set']['printerIpAddress'] == '192.168.1.5'
    assert updateRecorder['set']['commandId'] == responseBody['commandId']


def testQueuePrinterControlCommandParsesStringMetadata(monkeypatch):
    updateRecorder = {'set': None, 'update': []}
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(updateRecorder=updateRecorder),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.set_json(
        {
            'commandType': 'home_all',
            'printerSerial': 'SN-001',
            'metadata': '{"axis":"z"}',
        }
    )

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 202
    assert responseBody['ok'] is True
    assert updateRecorder['set']['printerSerial'] == 'SN-001'
    assert updateRecorder['set']['metadata'] == {'axis': 'z'}


def testQueuePrinterControlCommandRequiresPrinterIdentifier(monkeypatch):
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.set_json({'commandType': 'jog', 'metadata': {'axis': 'x', 'delta': 5}})

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 400
    assert responseBody['ok'] is False
    assert responseBody['error_type'] == 'ValidationError'
    assert 'printerIpAddress or printerSerial' in responseBody['message']


def testListPrinterControlCommandsReturnsPending(monkeypatch):
    commandSnapshots = [
        MockDocumentSnapshot(
            'cmd-1',
            {
                'commandId': 'cmd-1',
                'recipientId': 'recipient-123',
                'printerSerial': 'SN-001',
                'status': 'pending',
            },
        ),
        MockDocumentSnapshot(
            'cmd-2',
            {
                'commandId': 'cmd-2',
                'recipientId': 'recipient-123',
                'printerSerial': 'SN-001',
                'status': 'completed',
            },
        ),
        MockDocumentSnapshot(
            'cmd-3',
            {
                'commandId': 'cmd-3',
                'recipientId': 'recipient-123',
                'printerSerial': 'SN-999',
                'status': 'pending',
            },
        ),
    ]

    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(documentSnapshots=commandSnapshots),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {'recipientId': 'recipient-123', 'printerSerial': 'SN-001'}
    fakeRequest.clear_json()
    fakeRequest.method = 'GET'

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 200
    assert 'commands' in responseBody
    assert len(responseBody['commands']) == 1
    assert responseBody['commands'][0]['commandId'] == 'cmd-1'


def testListPrinterControlCommandsIgnoresPrinterIpAddressForFiltering(monkeypatch):
    commandSnapshots = [
        MockDocumentSnapshot(
            'cmd-serial',
            {
                'commandId': 'cmd-serial',
                'recipientId': 'recipient-123',
                'printerSerial': 'SN-001',
                'status': 'pending',
            },
        ),
        MockDocumentSnapshot(
            'cmd-other',
            {
                'commandId': 'cmd-other',
                'recipientId': 'recipient-123',
                'printerSerial': 'SN-002',
                'printerIpAddress': '10.0.0.9',
                'status': 'pending',
            },
        ),
    ]

    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(documentSnapshots=commandSnapshots),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {
        'recipientId': 'recipient-123',
        'printerSerial': 'SN-001',
        'printerIpAddress': '10.0.0.5',
    }
    fakeRequest.clear_json()
    fakeRequest.method = 'GET'

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 200
    assert 'commands' in responseBody
    assert [item['commandId'] for item in responseBody['commands']] == ['cmd-serial']


def testListPrinterControlCommandsFiltersBySerial(monkeypatch):
    commandSnapshots = [
        MockDocumentSnapshot(
            'cmd-first',
            {
                'commandId': 'cmd-first',
                'recipientId': 'recipient-123',
                'printerSerial': 'SN-001',
                'status': 'pending',
            },
        ),
        MockDocumentSnapshot(
            'cmd-second',
            {
                'commandId': 'cmd-second',
                'recipientId': 'recipient-123',
                'printerSerial': 'SN-002',
                'status': 'pending',
            },
        ),
    ]

    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(documentSnapshots=commandSnapshots),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {'recipientId': 'recipient-123', 'printerSerial': 'SN-002'}
    fakeRequest.clear_json()
    fakeRequest.method = 'GET'

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 200
    assert [item['commandId'] for item in responseBody['commands']] == ['cmd-second']
    assert responseBody['commands'][0]['printerSerial'] == 'SN-002'


def testListPrinterControlCommandsReturnsRecipientCommandsWhenSerialMissing(monkeypatch):
    commandSnapshots = [
        MockDocumentSnapshot(
            'cmd-first',
            {
                'commandId': 'cmd-first',
                'recipientId': 'recipient-123',
                'printerSerial': 'SN-001',
                'status': 'pending',
            },
        ),
        MockDocumentSnapshot(
            'cmd-second',
            {
                'commandId': 'cmd-second',
                'recipientId': 'recipient-123',
                'printerSerial': 'SN-002',
                'status': 'pending',
            },
        ),
        MockDocumentSnapshot(
            'cmd-third',
            {
                'commandId': 'cmd-third',
                'recipientId': 'recipient-456',
                'printerSerial': 'SN-003',
                'status': 'pending',
            },
        ),
        MockDocumentSnapshot(
            'cmd-ignored',
            {
                'commandId': 'cmd-ignored',
                'recipientId': 'recipient-123',
                'printerSerial': 'SN-001',
                'status': 'completed',
            },
        ),
    ]

    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(documentSnapshots=commandSnapshots),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {'recipientId': 'recipient-123'}
    fakeRequest.clear_json()
    fakeRequest.method = 'GET'

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 200
    returnedCommands = responseBody['commands']
    assert {item['commandId'] for item in returnedCommands} == {'cmd-first', 'cmd-second'}
    assert all('printerSerial' in item for item in returnedCommands)


def testListPrinterControlCommandsRequiresRecipientParameter(monkeypatch):
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {}
    fakeRequest.clear_json()
    fakeRequest.method = 'GET'

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 400
    assert responseBody['ok'] is False
    assert responseBody['error_type'] == 'ValidationError'
    assert 'recipientId' in responseBody['message']


def testListPrinterControlCommandsRejectsEmptyPrinterSerial(monkeypatch):
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {'recipientId': 'recipient-123', 'printerSerial': '   '}
    fakeRequest.clear_json()
    fakeRequest.method = 'GET'

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 400
    assert responseBody['ok'] is False
    assert responseBody['error_type'] == 'ValidationError'
    assert 'printerSerial' in responseBody['message']


def testAcknowledgePrinterControlCommandUpdatesStatus(monkeypatch):
    updateRecorder = {'set': None, 'update': []}
    commandSnapshot = MockDocumentSnapshot(
        'cmd-ack',
        {
            'commandId': 'cmd-ack',
            'recipientId': 'recipient-123',
            'printerSerial': 'SN-001',
            'status': 'pending',
        },
    )
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=commandSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {}
    fakeRequest.set_json({
        'commandId': 'cmd-ack',
        'recipientId': 'recipient-123',
        'printerSerial': 'SN-001',
    })

    responseBody, statusCode = main.acknowledgePrinterControlCommand()

    assert statusCode == 200
    assert responseBody['ok'] is True
    assert updateRecorder['update'], 'Expected Firestore update for acknowledgement'
    ackUpdate = updateRecorder['update'][0]
    assert ackUpdate['status'] == 'processing'
    assert ackUpdate['acknowledgedAt'] is firestoreModule.SERVER_TIMESTAMP
    assert ackUpdate['startedAt'] is firestoreModule.SERVER_TIMESTAMP

    fakeRequest.clear_json()
    fakeRequest.method = 'GET'
    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {'recipientId': 'recipient-123', 'printerSerial': 'SN-001'}

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 200
    assert responseBody['commands'] == []


def testAcknowledgePrinterControlCommandValidatesRecipient(monkeypatch):
    commandSnapshot = MockDocumentSnapshot(
        'cmd-ack',
        {
            'commandId': 'cmd-ack',
            'recipientId': 'recipient-123',
            'status': 'pending',
        },
    )
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(documentSnapshot=commandSnapshot),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {}
    fakeRequest.set_json({'commandId': 'cmd-ack', 'recipientId': 'recipient-999'})

    responseBody, statusCode = main.acknowledgePrinterControlCommand()

    assert statusCode == 403
    assert responseBody['ok'] is False
    assert responseBody['error_type'] == 'ForbiddenError'


def testSubmitPrinterControlResultStoresMessage(monkeypatch):
    updateRecorder = {'set': None, 'update': []}
    commandSnapshot = MockDocumentSnapshot(
        'cmd-result',
        {
            'commandId': 'cmd-result',
            'recipientId': 'recipient-123',
            'status': 'processing',
        },
    )
    mockClients = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(
            documentSnapshot=commandSnapshot, updateRecorder=updateRecorder
        ),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: mockClients)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {}
    fakeRequest.set_json(
        {
            'commandId': 'cmd-result',
            'recipientId': 'recipient-123',
            'status': 'completed',
            'message': 'All done',
        }
    )

    responseBody, statusCode = main.submitPrinterControlResult()

    assert statusCode == 200
    assert responseBody['ok'] is True
    assert updateRecorder['update'], 'Expected Firestore update for result submission'
    resultUpdate = updateRecorder['update'][0]
    assert resultUpdate['status'] == 'completed'
    assert resultUpdate['finishedAt'] is firestoreModule.SERVER_TIMESTAMP
    assert resultUpdate['message'] == 'All done'
    assert 'errorMessage' not in resultUpdate

    fakeRequest.clear_json()
    fakeRequest.method = 'GET'
    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {'recipientId': 'recipient-123'}

    responseBody, statusCode = main.queuePrinterControlCommand()

    assert statusCode == 200
    assert responseBody['commands'] == []


def testSubmitPrinterControlResultMissingCommandReturnsNotFound(monkeypatch):
    emptyClient = main.ClientBundle(
        storageClient=MockStorageClient(),
        firestoreClient=MockFirestoreClient(),
        kmsClient=MockEncryptClient({'sensitive': 'value'}),
        kmsKeyPath='projects/test/locations/test/keyRings/test/cryptoKeys/test',
        gcsBucketName='test-bucket',
    )

    monkeypatch.setattr(main, 'getClients', lambda: emptyClient)
    monkeypatch.setattr(main, 'validPrinterApiKeys', {'control-key'})

    fakeRequest.headers = {'X-API-Key': 'control-key'}
    fakeRequest.args = {}
    fakeRequest.set_json({'commandId': 'missing', 'status': 'failed'})

    responseBody, statusCode = main.submitPrinterControlResult()

    assert statusCode == 404
    assert responseBody['ok'] is False
    assert responseBody['error_type'] == 'NotFound'


def testUploadFileReportsMissingEnvironment(monkeypatch):
    def raiseMissing():
        raise main.MissingEnvironmentError(['GCP_PROJECT_ID'])

    monkeypatch.setattr(main, 'getClients', raiseMissing)

    responseBody, statusCode = main.uploadFile()

    assert statusCode == 503
    assert responseBody['error'] == 'Missing environment configuration'
    assert responseBody['missingVariables'] == ['GCP_PROJECT_ID']


def testUploadFileReportsPermissionFailure(monkeypatch):
    def raisePermission():
        raise main.ClientInitializationError('Google Cloud clients', Exception('Permission denied'))

    monkeypatch.setattr(main, 'getClients', raisePermission)

    responseBody, statusCode = main.uploadFile()

    assert statusCode == 500
    assert responseBody['error'] == 'Failed to initialize Google Cloud clients'
    assert responseBody['detail'] == 'Permission denied'


def testParseJsonObjectFieldParsesKeyValueFallback():
    parsedValue, errorResponse = main.parseJsonObjectField('{printJob:demo}', 'unencrypted_data')

    assert errorResponse is None
    assert parsedValue == {'printJob': 'demo'}


def testParseJsonObjectFieldParsesEqualsFallback():
    parsedValue, errorResponse = main.parseJsonObjectField('secret=1234', 'encrypted_data_payload')

    assert errorResponse is None
    assert parsedValue == {'secret': '1234'}
