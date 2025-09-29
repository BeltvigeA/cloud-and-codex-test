import json
import os
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

fakeFlaskModule = ModuleType('flask')
werkzeugModule = ModuleType('werkzeug')
werkzeugUtilsModule = ModuleType('werkzeug.utils')


class DummyFlask:
    def __init__(self, _name):
        self.name = _name

    def route(self, _rule, methods=None):  # pylint: disable=unused-argument
        def decorator(function):
            return function

        return decorator


def dummyJsonify(payload):
    return payload


fakeRequest = SimpleNamespace(files={}, form={})
fakeFlaskModule.Flask = DummyFlask
fakeFlaskModule.jsonify = dummyJsonify
fakeFlaskModule.request = fakeRequest
sys.modules['flask'] = fakeFlaskModule


def secureFilename(value):
    sanitized = ''.join(
        character for character in value if character.isalnum() or character in {'.', '_', '-'}
    )
    return sanitized.strip(' .')


werkzeugUtilsModule.secure_filename = secureFilename
werkzeugModule.utils = werkzeugUtilsModule
sys.modules['werkzeug'] = werkzeugModule
sys.modules['werkzeug.utils'] = werkzeugUtilsModule

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
exceptionsModule = ModuleType('google.api_core.exceptions')


class DummyGoogleApiCallError(Exception):
    def __init__(self, message='error'):
        super().__init__(message)
        self.message = message


exceptionsModule.GoogleAPICallError = DummyGoogleApiCallError
apiCoreModule = ModuleType('google.api_core')
apiCoreModule.exceptions = exceptionsModule

sys.modules['google'] = googleModule
sys.modules['google.cloud'] = cloudModule
sys.modules['google.cloud.firestore'] = firestoreModule
sys.modules['google.cloud.firestore_v1'] = firestoreV1Module
sys.modules['google.cloud.storage'] = storageModule
sys.modules['google.cloud.kms_v1'] = kmsModule
sys.modules['google.api_core'] = apiCoreModule
sys.modules['google.api_core.exceptions'] = exceptionsModule

googleModule.cloud = cloudModule
cloudModule.firestore = firestoreModule
cloudModule.storage = storageModule
cloudModule.kms_v1 = kmsModule

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('GCP_PROJECT_ID', 'test-project')
os.environ.setdefault('GCS_BUCKET_NAME', 'test-bucket')
os.environ.setdefault('KMS_KEY_RING', 'test-key-ring')
os.environ.setdefault('KMS_KEY_NAME', 'test-key')
os.environ.setdefault('KMS_LOCATION', 'test-location')
import main


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
    def __init__(self, updateRecorder):
        self.updateRecorder = updateRecorder

    def set(self, metadata):
        self.updateRecorder['set'] = metadata

    def update(self, payload):
        self.updateRecorder['update'].append(payload)


class MockQuery:
    def __init__(self, documentSnapshot):
        self.documentSnapshot = documentSnapshot

    def limit(self, _count):
        return self

    def stream(self):
        return [self.documentSnapshot]


class MockCollection:
    def __init__(self, documentSnapshot, updateRecorder):
        self.documentSnapshot = documentSnapshot
        self.updateRecorder = updateRecorder

    def document(self, _docId):
        return MockDocument(self.updateRecorder)

    def where(self, _field, _operator, _value):
        return MockQuery(self.documentSnapshot)


class MockFirestoreClient:
    def __init__(self, documentSnapshot=None, updateRecorder=None):
        self.documentSnapshot = documentSnapshot
        self.updateRecorder = updateRecorder or {'set': None, 'update': []}

    def collection(self, _name):
        if self.documentSnapshot is None:
            return MockCollection(None, self.updateRecorder)
        return MockCollection(self.documentSnapshot, self.updateRecorder)


class MockDocumentSnapshot:
    def __init__(self, docId, metadata):
        self.id = docId
        self._metadata = metadata

    def to_dict(self):
        return self._metadata


@pytest.fixture(autouse=True)
def resetClients(monkeypatch):
    monkeypatch.setattr(main, 'storageClient', MockStorageClient())
    monkeypatch.setattr(main, 'kmsClient', MockEncryptClient({'sensitive': 'value'}))
    fakeRequest.files = {}
    fakeRequest.form = {}
    yield


def testUploadFileStoresExpiryMetadata(monkeypatch):
    metadataRecorder = {'set': None, 'update': []}
    monkeypatch.setattr(main, 'firestoreClient', MockFirestoreClient(updateRecorder=metadataRecorder))
    monkeypatch.setattr(main, 'generateFetchToken', lambda: 'testFetchToken')

    fakeRequest.files = {'file': MockUploadFile(b'file-contents', 'test.gcode')}
    fakeRequest.form = {
        'unencrypted_data': json.dumps({'visible': 'info'}),
        'encrypted_data_payload': json.dumps({'secure': 'payload'}),
        'recipient_id': 'recipient123',
    }

    responseBody, statusCode = main.uploadFile()

    assert statusCode == 200
    assert responseBody['fetchToken'] == 'testFetchToken'
    storedMetadata = metadataRecorder['set']
    assert storedMetadata is not None
    assert storedMetadata['fetchTokenConsumed'] is False
    assert storedMetadata['fetchTokenExpiry'] > datetime.now(timezone.utc)


def testFetchFileFirstUseSuccess(monkeypatch):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': False,
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}
    monkeypatch.setattr(
        main,
        'firestoreClient',
        MockFirestoreClient(documentSnapshot=documentSnapshot, updateRecorder=updateRecorder),
    )

    fakeRequest.files = {}
    fakeRequest.form = {}

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 200
    assert responseBody['decryptedData'] == {'sensitive': 'value'}
    assert responseBody['unencryptedData'] == {'visible': 'info'}

    assert updateRecorder['update'], 'Expected Firestore update to be recorded'
    updatePayload = updateRecorder['update'][0]
    assert updatePayload['fetchToken'] is main.DELETE_FIELD
    assert updatePayload['fetchTokenExpiry'] is main.DELETE_FIELD
    assert updatePayload['fetchTokenConsumed'] is True


def testFetchFileRejectsConsumedToken(monkeypatch):
    metadata = {
        'encryptedData': '7b7d',
        'unencryptedData': {'visible': 'info'},
        'gcsPath': 'recipient123/file.gcode',
        'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=5),
        'fetchTokenConsumed': True,
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}
    monkeypatch.setattr(
        main,
        'firestoreClient',
        MockFirestoreClient(documentSnapshot=documentSnapshot, updateRecorder=updateRecorder),
    )

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
    }
    documentSnapshot = MockDocumentSnapshot('doc123', metadata)
    updateRecorder = {'set': None, 'update': []}
    monkeypatch.setattr(
        main,
        'firestoreClient',
        MockFirestoreClient(documentSnapshot=documentSnapshot, updateRecorder=updateRecorder),
    )

    fakeRequest.files = {}
    fakeRequest.form = {}

    responseBody, statusCode = main.fetchFile('testFetchToken')

    assert statusCode == 410
    assert 'expired' in responseBody['error']
    assert not updateRecorder['update'], 'Update should not be called for expired tokens'
