import importlib
from pathlib import Path
import sys
import types
import json
from io import BytesIO
import uuid

import pytest


class FakeBlob:
    def __init__(self, storageClient, bucketName, objectName):
        self.storageClient = storageClient
        self.bucketName = bucketName
        self.objectName = objectName

    def upload_from_file(self, fileObject):
        fileBytes = fileObject.read()
        if hasattr(fileObject, "seek"):
            fileObject.seek(0)
        self.storageClient.blobContents[(self.bucketName, self.objectName)] = fileBytes

    def generate_signed_url(self, version, expiration, method):
        return f"https://storage.example/{self.bucketName}/{self.objectName}?method={method}&expires={int(expiration.total_seconds())}"


class FakeBucket:
    def __init__(self, storageClient, name):
        self.storageClient = storageClient
        self.name = name

    def blob(self, objectName):
        fakeBlob = FakeBlob(self.storageClient, self.name, objectName)
        self.storageClient.createdBlobs.append((self.name, objectName))
        return fakeBlob


class FakeStorageClient:
    def __init__(self, project=None):
        self.project = project
        self.blobContents = {}
        self.createdBlobs = []

    def bucket(self, name):
        return FakeBucket(self, name)


class FakeDocumentReference:
    def __init__(self, fakeStore, collectionName, documentId):
        self.fakeStore = fakeStore
        self.collectionName = collectionName
        self.documentId = documentId

    def set(self, data):
        self.fakeStore[(self.collectionName, self.documentId)] = dict(data)

    def update(self, updates):
        existing = self.fakeStore.get((self.collectionName, self.documentId), {})
        existing.update(updates)
        self.fakeStore[(self.collectionName, self.documentId)] = existing


class FakeDocumentSnapshot:
    def __init__(self, documentId, data):
        self.id = documentId
        self._data = dict(data)

    def to_dict(self):
        return dict(self._data)


class FakeQuery:
    def __init__(self, results):
        self.results = results

    def limit(self, _limit):
        return self

    def stream(self):
        return iter(self.results)


class FakeCollection:
    def __init__(self, fakeClient, name):
        self.fakeClient = fakeClient
        self.name = name

    def document(self, documentId):
        return FakeDocumentReference(self.fakeClient.fakeStore, self.name, documentId)

    def where(self, field, _operator, value):
        matches = []
        for (collectionName, documentId), data in self.fakeClient.fakeStore.items():
            if collectionName == self.name and data.get(field) == value:
                matches.append(FakeDocumentSnapshot(documentId, data))
        return FakeQuery(matches)

    def add(self, data):
        documentId = uuid.uuid4().hex
        self.fakeClient.fakeStore[(self.name, documentId)] = dict(data)
        return FakeDocumentReference(self.fakeClient.fakeStore, self.name, documentId), None


class FakeFirestoreClient:
    def __init__(self, project=None):
        self.project = project
        self.fakeStore = {}

    def collection(self, name):
        return FakeCollection(self, name)


class FakeEncryptResponse:
    def __init__(self, ciphertext):
        self.ciphertext = ciphertext


class FakeDecryptResponse:
    def __init__(self, plaintext):
        self.plaintext = plaintext


class FakeKmsClient:
    def __init__(self):
        self.keyPathCalls = []
        self.encryptCalls = []
        self.decryptCalls = []

    def crypto_key_path(self, project, location, keyRing, keyName):
        keyPath = f"projects/{project}/locations/{location}/keyRings/{keyRing}/cryptoKeys/{keyName}"
        self.keyPathCalls.append(keyPath)
        return keyPath

    def encrypt(self, request):
        plaintext = request["plaintext"]
        self.encryptCalls.append(plaintext)
        reversedBytes = plaintext[::-1]
        return FakeEncryptResponse(reversedBytes)

    def decrypt(self, request):
        ciphertext = request["ciphertext"]
        self.decryptCalls.append(ciphertext)
        reversedBytes = ciphertext[::-1]
        return FakeDecryptResponse(reversedBytes)


@pytest.fixture
def appWithFakes(monkeypatch):
    projectRoot = Path(__file__).resolve().parents[1]
    if str(projectRoot) not in sys.path:
        sys.path.insert(0, str(projectRoot))
    googleModule = types.ModuleType("google") if "google" not in sys.modules else sys.modules["google"]
    googleModule.__path__ = []
    sys.modules["google"] = googleModule

    cloudModule = types.ModuleType("google.cloud") if "google.cloud" not in sys.modules else sys.modules["google.cloud"]
    cloudModule.__path__ = []
    sys.modules["google.cloud"] = cloudModule

    flaskModule = types.ModuleType("flask")

    class FakeResponse:
        def __init__(self, payload, status=200):
            self.payload = payload
            self.status_code = status

        def get_json(self):
            return self.payload

    class FakeFileStorage:
        def __init__(self, stream, filename):
            self.stream = stream
            self.filename = filename

        def read(self, size=-1):
            return self.stream.read(size)

        def seek(self, offset, whence=0):
            return self.stream.seek(offset, whence)

    class FakeRequest:
        def __init__(self):
            self.files = {}
            self.form = {}
            self.headers = {}
            self._json = None
            self.is_json = False

        def get_json(self):
            return self._json

        def reset(self):
            self.files = {}
            self.form = {}
            self.headers = {}
            self._json = None
            self.is_json = False

    fakeRequest = FakeRequest()

    def jsonify(*args, **kwargs):
        if args and kwargs:
            raise TypeError('Cannot mix args and kwargs in jsonify stub')
        payload = args[0] if args else kwargs
        return FakeResponse(payload, status=200)

    class FakeFlask:
        def __init__(self, importName):
            self.import_name = importName
            self._routes = {"GET": [], "POST": []}
            self.config = {}

        def route(self, rule, methods=None):
            methods = methods or ["GET"]

            def decorator(func):
                for method in methods:
                    self._routes.setdefault(method.upper(), []).append((rule, func))
                return func

            return decorator

        def test_client(self):
            flaskApp = self

            class FakeTestClient:
                def _match_route(self, method, path):
                    for rule, view in flaskApp._routes.get(method.upper(), []):
                        if "<" in rule:
                            prefix, param = rule.split("<", 1)
                            paramName = param.rstrip(">")
                            if path.startswith(prefix):
                                return view, {paramName: path[len(prefix):]}
                        elif rule == path:
                            return view, {}
                    raise ValueError(f"No route for {method} {path}")

                def _prepare_request(self, method, path, data=None, headers=None, jsonPayload=None, content_type=None):
                    fakeRequest.reset()
                    fakeRequest.headers = headers or {}
                    if jsonPayload is not None:
                        fakeRequest._json = jsonPayload
                        fakeRequest.is_json = True
                    else:
                        fakeRequest.is_json = False
                    if data:
                        for key, value in data.items():
                            if key == "file":
                                stream, filename = value
                                if isinstance(stream, (bytes, bytearray)):
                                    stream = BytesIO(stream)
                                fakeRequest.files[key] = FakeFileStorage(stream, filename)
                            else:
                                fakeRequest.form[key] = value

                def _invoke(self, method, path, data=None, headers=None, jsonPayload=None, content_type=None):
                    viewFunc, routeParams = self._match_route(method, path)
                    self._prepare_request(method, path, data=data, headers=headers, jsonPayload=jsonPayload, content_type=content_type)
                    result = viewFunc(**routeParams)
                    status = None
                    if isinstance(result, tuple):
                        response, status = result
                    else:
                        response = result
                    if not isinstance(response, FakeResponse):
                        response = FakeResponse(response)
                    if status is None:
                        status = response.status_code
                    response.status_code = status
                    return response

                def post(self, path, data=None, headers=None, json=None, content_type=None):
                    return self._invoke("POST", path, data=data, headers=headers, jsonPayload=json, content_type=content_type)

                def get(self, path, headers=None):
                    return self._invoke("GET", path, headers=headers)

            return FakeTestClient()

    flaskModule.Flask = FakeFlask
    flaskModule.jsonify = jsonify
    flaskModule.request = fakeRequest
    sys.modules["flask"] = flaskModule

    storageModule = types.ModuleType("google.cloud.storage")
    storageModule.Client = object
    firestoreModule = types.ModuleType("google.cloud.firestore")
    firestoreModule.Client = object
    kmsModule = types.ModuleType("google.cloud.kms_v1")
    kmsModule.KeyManagementServiceClient = object

    sys.modules["google.cloud.storage"] = storageModule
    sys.modules["google.cloud.firestore"] = firestoreModule
    sys.modules["google.cloud.kms_v1"] = kmsModule

    cloudModule.storage = storageModule
    cloudModule.firestore = firestoreModule
    cloudModule.kms_v1 = kmsModule

    apiCoreModule = types.ModuleType("google.api_core") if "google.api_core" not in sys.modules else sys.modules["google.api_core"]
    apiCoreModule.__path__ = []
    sys.modules["google.api_core"] = apiCoreModule

    exceptionsModule = types.ModuleType("google.api_core.exceptions")

    class FakeGoogleApiCallError(Exception):
        def __init__(self, message=""):
            super().__init__(message)
            self.message = message

    exceptionsModule.GoogleAPICallError = FakeGoogleApiCallError
    sys.modules["google.api_core.exceptions"] = exceptionsModule
    apiCoreModule.exceptions = exceptionsModule

    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("KMS_KEY_RING", "test-ring")
    monkeypatch.setenv("KMS_KEY_NAME", "test-key")
    monkeypatch.setenv("KMS_LOCATION", "global")
    monkeypatch.setenv("FIRESTORE_COLLECTION_FILES", "files")
    monkeypatch.setenv("FIRESTORE_COLLECTION_PRINTER_STATUS", "printer_status_updates")
    monkeypatch.setenv("API_KEYS_PRINTER_STATUS", "printerKey")
    monkeypatch.setenv("PORT", "8080")

    fakeStorageClient = FakeStorageClient()
    fakeFirestoreClient = FakeFirestoreClient()
    fakeKmsClient = FakeKmsClient()

    from google.cloud import firestore as realFirestore
    from google.cloud import storage as realStorage
    from google.cloud import kms_v1 as realKms

    monkeypatch.setattr(realStorage, "Client", lambda project=None: fakeStorageClient)
    monkeypatch.setattr(realFirestore, "Client", lambda project=None: fakeFirestoreClient)
    monkeypatch.setattr(realKms, "KeyManagementServiceClient", lambda: fakeKmsClient)

    import main

    importlib.reload(main)

    main.storage_client = fakeStorageClient
    main.db = fakeFirestoreClient
    main.kms_client = fakeKmsClient
    main.kms_key_path = fakeKmsClient.crypto_key_path(
        "test-project", "global", "test-ring", "test-key"
    )
    main.firestore.SERVER_TIMESTAMP = "server-timestamp"

    flaskApp = main.app
    flaskApp.config["fakeStorageClient"] = fakeStorageClient
    flaskApp.config["fakeFirestoreClient"] = fakeFirestoreClient
    flaskApp.config["fakeKmsClient"] = fakeKmsClient

    yield flaskApp

    importlib.reload(main)
