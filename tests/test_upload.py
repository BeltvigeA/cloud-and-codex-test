import json
import sys
import types
from pathlib import Path
import importlib.util
import io

import pytest


class DummyBlob:
    def __init__(self):
        self.uploadedPayloads = []
        self.uploadCallCount = 0

    def upload_from_file(self, fileHandle):
        # Read the file to simulate upload and store content for assertions if needed.
        self.uploadCallCount += 1
        self.uploadedPayloads.append(fileHandle.read())
        fileHandle.seek(0)

    def exists(self, _client=None):
        return False

    def generate_signed_url(self, **_kwargs):
        return 'http://example.com/download'


class DummyBucket:
    def __init__(self):
        self.createdBlobs = []

    def blob(self, _name):
        blob = DummyBlob()
        self.createdBlobs.append(blob)
        return blob


class DummyStorageClient:
    def __init__(self, project=None):
        self.project = project
        self.bucketInstances = []

    def bucket(self, _name):
        bucket = DummyBucket()
        self.bucketInstances.append(bucket)
        return bucket


class DummyCollection:
    def document(self, _docId):
        return self

    def set(self, _metadata):
        return None

    def update(self, _data):
        return None

    def where(self, *_args, **_kwargs):
        return self

    def limit(self, _value):
        return self

    def stream(self):
        return []


class DummyFirestoreClient:
    def __init__(self, project=None):
        self.project = project

    def collection(self, _name):
        return DummyCollection()


class DummyEncryptResponse:
    def __init__(self):
        self.ciphertext = b'\x01\x02'


class DummyDecryptResponse:
    plaintext = b'{}'


class DummyKmsClient:
    def crypto_key_path(self, project, location, keyRing, keyName):
        return f"{project}/{location}/{keyRing}/{keyName}"

    def encrypt(self, request):
        assert request['name']
        assert request['plaintext']
        return DummyEncryptResponse()

    def decrypt(self, request):
        assert request['name']
        assert request['ciphertext']
        return DummyDecryptResponse()


class DummyGoogleApiCallError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class DummyFileStorage:
    def __init__(self, data, filename, mimetype='application/octet-stream'):
        if isinstance(data, io.BytesIO):
            self.stream = data
        else:
            self.stream = io.BytesIO(data)
        self.filename = filename
        self.mimetype = mimetype

    def read(self, size=-1):
        return self.stream.read(size)

    def seek(self, position, whence=0):
        return self.stream.seek(position, whence)


def installGoogleStubs(monkeypatch):
    class DummyRequest:
        def __init__(self):
            self.files = {}
            self.form = {}

    class DummyResponse(dict):
        def get_json(self):
            return dict(self)

    class DummyFlask:
        def __init__(self, _name):
            self.name = _name
            self.config = {}

        def route(self, _rule, methods=None):
            del methods

            def decorator(func):
                return func

            return decorator

    def secureFilename(value):
        sanitized = ''.join(character for character in value if character.isalnum() or character in {'.', '_', '-'})
        return sanitized.strip(' .')

    googleModule = types.ModuleType('google')
    cloudModule = types.ModuleType('google.cloud')
    storageModule = types.ModuleType('google.cloud.storage')
    firestoreModule = types.ModuleType('google.cloud.firestore')
    kmsModule = types.ModuleType('google.cloud.kms_v1')
    apiCoreModule = types.ModuleType('google.api_core')
    exceptionsModule = types.ModuleType('google.api_core.exceptions')
    flaskModule = types.ModuleType('flask')
    werkzeugModule = types.ModuleType('werkzeug')
    werkzeugUtilsModule = types.ModuleType('werkzeug.utils')

    storageModule.Client = DummyStorageClient
    firestoreModule.Client = DummyFirestoreClient
    firestoreModule.SERVER_TIMESTAMP = object()
    kmsModule.KeyManagementServiceClient = DummyKmsClient
    exceptionsModule.GoogleAPICallError = DummyGoogleApiCallError
    exceptionsModule.Forbidden = type('DummyForbidden', (DummyGoogleApiCallError,), {})
    exceptionsModule.PermissionDenied = type(
        'DummyPermissionDenied', (DummyGoogleApiCallError,), {}
    )
    exceptionsModule.Unauthorized = type(
        'DummyUnauthorized', (DummyGoogleApiCallError,), {}
    )
    dummyRequest = DummyRequest()
    flaskModule.Flask = DummyFlask
    flaskModule.jsonify = lambda payload: DummyResponse(payload)
    flaskModule.request = dummyRequest
    werkzeugModule.utils = werkzeugUtilsModule
    werkzeugUtilsModule.secure_filename = secureFilename

    googleModule.cloud = cloudModule
    googleModule.api_core = apiCoreModule
    cloudModule.storage = storageModule
    cloudModule.firestore = firestoreModule
    cloudModule.kms_v1 = kmsModule
    apiCoreModule.exceptions = exceptionsModule

    sys.modules['google'] = googleModule
    sys.modules['google.cloud'] = cloudModule
    sys.modules['google.cloud.storage'] = storageModule
    sys.modules['google.cloud.firestore'] = firestoreModule
    sys.modules['google.cloud.kms_v1'] = kmsModule
    sys.modules['google.api_core'] = apiCoreModule
    sys.modules['google.api_core.exceptions'] = exceptionsModule
    sys.modules['flask'] = flaskModule
    sys.modules['werkzeug'] = werkzeugModule
    sys.modules['werkzeug.utils'] = werkzeugUtilsModule

    monkeypatch.setenv('GCP_PROJECT_ID', 'test-project')
    monkeypatch.setenv('GCS_BUCKET_NAME', 'test-bucket')
    monkeypatch.setenv('KMS_KEY_RING', 'test-ring')
    monkeypatch.setenv('KMS_KEY_NAME', 'test-key')
    monkeypatch.setenv('KMS_LOCATION', 'test-location')


@pytest.fixture()
def appModule(monkeypatch):
    installGoogleStubs(monkeypatch)
    if 'main' in sys.modules:
        del sys.modules['main']

    spec = importlib.util.spec_from_file_location('main', Path(__file__).resolve().parent.parent / 'main.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['main'] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.app.config['TESTING'] = True
    yield module


def buildRequest(module, fileStorage, formValues):
    cachedClients = getattr(module, 'cachedClients', None)
    if cachedClients and hasattr(cachedClients.storageClient, 'bucketInstances'):
        cachedClients.storageClient.bucketInstances.clear()
    module.request.files = {'file': fileStorage}
    module.request.form = formValues


def extractResponse(result):
    response, statusCode = result
    return response.get_json(), statusCode


def test_uploadAcceptsAllowedExtension(appModule):
    buildRequest(
        appModule,
        DummyFileStorage(b'gcode data', 'print_job.gcode'),
        {
            'unencrypted_data': json.dumps({'info': 'value'}),
            'encrypted_data_payload': json.dumps({'secret': 'value'}),
            'recipient_id': 'recipient123',
            'product_id': '123e4567-e89b-12d3-a456-426614174000',
        },
    )

    payload, statusCode = extractResponse(appModule.uploadFile())

    assert statusCode == 200
    assert payload['message'] == 'File uploaded successfully'


def test_uploadRejectsDisallowedExtension(appModule):
    buildRequest(
        appModule,
        DummyFileStorage(b'invalid data', 'notes.txt'),
        {
            'unencrypted_data': '{}',
            'encrypted_data_payload': '{}',
            'recipient_id': 'recipient123',
            'product_id': '123e4567-e89b-12d3-a456-426614174000',
        },
    )

    payload, statusCode = extractResponse(appModule.uploadFile())

    assert statusCode == 400
    assert 'Unsupported file type' in payload['error']


def test_uploadRejectsDisallowedMimeType(appModule):
    buildRequest(
        appModule,
        DummyFileStorage(b'gcode data', 'build.gcode', mimetype='image/png'),
        {
            'unencrypted_data': '{}',
            'encrypted_data_payload': '{}',
            'recipient_id': 'recipient123',
            'product_id': '123e4567-e89b-12d3-a456-426614174000',
        },
    )

    payload, statusCode = extractResponse(appModule.uploadFile())

    assert statusCode == 400
    assert 'Unsupported MIME type' in payload['error']


def test_uploadAcceptsUppercaseExtension(appModule):
    buildRequest(
        appModule,
        DummyFileStorage(b'gcode data', 'MODEL.GCO'),
        {
            'unencrypted_data': json.dumps({'info': 'value'}),
            'encrypted_data_payload': json.dumps({'secret': 'value'}),
            'recipient_id': 'recipient123',
            'product_id': '123e4567-e89b-12d3-a456-426614174000',
        },
    )

    payload, statusCode = extractResponse(appModule.uploadFile())

    assert statusCode == 200
    assert payload['message'] == 'File uploaded successfully'

    cachedClients = appModule.cachedClients
    assert cachedClients is not None
    assert cachedClients.storageClient.bucketInstances
    createdBucket = cachedClients.storageClient.bucketInstances[-1]
    assert createdBucket.createdBlobs
    assert createdBucket.createdBlobs[-1].uploadCallCount == 1


def test_uploadAcceptsDoubleEncodedJson(appModule):
    doubleEncodedValue = json.dumps(json.dumps({'info': 'value'}))
    buildRequest(
        appModule,
        DummyFileStorage(b'gcode data', 'print_job.gcode'),
        {
            'unencrypted_data': doubleEncodedValue,
            'encrypted_data_payload': doubleEncodedValue,
            'recipient_id': 'recipient123',
            'product_id': '123e4567-e89b-12d3-a456-426614174000',
        },
    )

    payload, statusCode = extractResponse(appModule.uploadFile())

    assert statusCode == 200
    assert payload['message'] == 'File uploaded successfully'


def test_uploadRejectsNonObjectJsonMetadata(appModule):
    buildRequest(
        appModule,
        DummyFileStorage(b'gcode data', 'print_job.gcode'),
        {
            'unencrypted_data': '[]',
            'encrypted_data_payload': '{}',
            'recipient_id': 'recipient123',
            'product_id': '123e4567-e89b-12d3-a456-426614174000',
        },
    )

    payload, statusCode = extractResponse(appModule.uploadFile())

    assert statusCode == 400
    assert payload['error'] == 'Invalid JSON format for associated data'


def test_uploadRejectsExtensionBeforeUpload(appModule):
    buildRequest(
        appModule,
        DummyFileStorage(b'invalid data', '../../escape.exe'),
        {
            'unencrypted_data': '{}',
            'encrypted_data_payload': '{}',
            'recipient_id': 'recipient123',
            'product_id': '123e4567-e89b-12d3-a456-426614174000',
        },
    )

    payload, statusCode = extractResponse(appModule.uploadFile())

    assert statusCode == 400
    assert 'Unsupported file type' in payload['error']
    cachedClients = appModule.cachedClients
    assert cachedClients is not None
    assert cachedClients.storageClient.bucketInstances == []
