import json
import logging
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from flask import Flask, jsonify, request
from google.api_core.exceptions import (
    Forbidden,
    GoogleAPICallError,
    PermissionDenied,
    Unauthorized,
)
try:  # pragma: no cover - optional dependency handling
    from google.auth import default as googleAuthDefault
except (ImportError, AttributeError):  # pragma: no cover - fallback when google-auth is unavailable in tests
    googleAuthDefault = None  # type: ignore[assignment]
from google.auth.exceptions import GoogleAuthError

try:  # pragma: no cover - optional dependency handling
    from google.auth.transport.requests import Request
except ImportError:  # pragma: no cover - fallback when google-auth is unavailable in tests
    Request = None  # type: ignore[assignment]
from google.cloud import firestore, kms_v1, storage
try:  # pragma: no cover - optional dependency handling
    from google.cloud import secretmanager
except ImportError:  # pragma: no cover - fallback when secret manager is unavailable in tests
    secretmanager = None  # type: ignore[assignment]
from google.cloud.firestore_v1 import DELETE_FIELD
from werkzeug.utils import secure_filename


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)


allowedUploadExtensions = {'.3mf', '.gcode', '.gco'}
allowedUploadMimeTypes = {
    'application/octet-stream',
    'application/x-gcode',
    'text/plain',
    'model/3mf',
}


firestoreCollectionFiles = os.environ.get('FIRESTORE_COLLECTION_FILES', 'files')
firestoreCollectionPrinterStatus = os.environ.get('FIRESTORE_COLLECTION_PRINTER_STATUS', 'printer_status_updates')
validPrinterApiKeys: Set[str] = set()
port = int(os.environ.get('PORT', '8080'))
fetchTokenTtlMinutes = int(os.environ.get('FETCH_TOKEN_TTL_MINUTES', '15'))


class MissingEnvironmentError(RuntimeError):
    def __init__(self, missingVariables: List[str]):
        self.missingVariables = missingVariables
        message = ', '.join(sorted(missingVariables))
        super().__init__(f'Missing environment variables: {message}')


class ClientInitializationError(RuntimeError):
    def __init__(self, component: str, error: Exception):
        self.component = component
        self.detail = str(error)
        super().__init__(f'Failed to initialize {component}: {error}')


@dataclass(frozen=True)
class ClientBundle:
    storageClient: storage.Client
    firestoreClient: firestore.Client
    kmsClient: kms_v1.KeyManagementServiceClient
    kmsKeyPath: str
    gcsBucketName: str


cachedClients: Optional[ClientBundle] = None


def parsePrinterApiKeyString(rawKeys: str) -> Set[str]:
    return {
        apiKey.strip()
        for apiKey in re.split(r'[,\r\n]+', rawKeys)
        if apiKey.strip()
    }


def loadPrinterApiKeys(secretManagerClient=None) -> Set[str]:
    environmentValue = os.environ.get('API_KEYS_PRINTER_STATUS')
    if environmentValue:
        logging.info('Loaded printer API keys from API_KEYS_PRINTER_STATUS environment variable.')
        return parsePrinterApiKeyString(environmentValue)

    secretPath = os.environ.get('SECRET_MANAGER_API_KEYS_PATH')
    if not secretPath:
        logging.warning(
            'Printer API keys are not configured. Set API_KEYS_PRINTER_STATUS or SECRET_MANAGER_API_KEYS_PATH.'
        )
        return set()

    if secretmanager is None:
        logging.error(
            'google.cloud.secretmanager is unavailable. Unable to load printer API keys from %s.',
            secretPath,
        )
        return set()

    try:
        client = secretManagerClient or secretmanager.SecretManagerServiceClient()
        secretResponse = client.access_secret_version(name=secretPath)
        secretPayload = secretResponse.payload.data.decode('utf-8')
        if not secretPayload.strip():
            logging.warning('Secret Manager secret %s did not contain any printer API keys.', secretPath)
            return set()

        logging.info('Loaded printer API keys from Secret Manager path %s.', secretPath)
        return parsePrinterApiKeyString(secretPayload)
    except Exception as error:  # pragma: no cover - defensive logging for unexpected client failures
        logging.error('Failed to load printer API keys from Secret Manager path %s: %s', secretPath, error)
        return set()


validPrinterApiKeys = loadPrinterApiKeys()


def tryParseKeyValueObject(rawValue: str) -> Optional[dict]:
    sanitizedValue = rawValue.strip()
    if not sanitizedValue:
        return None

    if sanitizedValue[0] == '{' and sanitizedValue[-1] == '}':
        sanitizedValue = sanitizedValue[1:-1].strip()

    if not sanitizedValue:
        return {}

    segments = []
    currentSegment = []
    insideQuotes = False
    quoteCharacter = ''
    escapeNext = False
    nestingDepth = 0

    for character in sanitizedValue:
        if escapeNext:
            currentSegment.append(character)
            escapeNext = False
            continue

        if character == '\\':
            currentSegment.append(character)
            escapeNext = True
            continue

        if character in {'"', "'"}:
            if insideQuotes and character == quoteCharacter:
                insideQuotes = False
            elif not insideQuotes:
                insideQuotes = True
                quoteCharacter = character
            currentSegment.append(character)
            continue

        if character in {'{', '['} and not insideQuotes:
            nestingDepth += 1
        elif character in {'}', ']'} and not insideQuotes and nestingDepth > 0:
            nestingDepth -= 1

        if character in {',', ';', '&'} and not insideQuotes and nestingDepth == 0:
            segment = ''.join(currentSegment).strip()
            if segment:
                segments.append(segment)
            currentSegment = []
            continue

        currentSegment.append(character)

    finalSegment = ''.join(currentSegment).strip()
    if finalSegment:
        segments.append(finalSegment)

    if not segments:
        return None

    parsedObject = {}
    for segment in segments:
        separatorIndex = -1
        for separator in (':', '='):
            if separator in segment:
                separatorIndex = segment.find(separator)
                break

        if separatorIndex == -1:
            return None

        rawKey = segment[:separatorIndex].strip().strip("\"'")
        rawValuePart = segment[separatorIndex + 1 :].strip()

        if not rawKey:
            return None

        if rawValuePart and rawValuePart[0] in {'"', "'"} and rawValuePart[-1] == rawValuePart[0]:
            rawValuePart = rawValuePart[1:-1]

        try:
            normalizedValue = bytes(rawValuePart, 'utf-8').decode('unicode_escape')
        except UnicodeDecodeError:
            normalizedValue = rawValuePart

        parsedObject[rawKey] = normalizedValue

    return parsedObject if parsedObject else None


def parseJsonObjectField(rawValue: str, fieldName: str) -> Tuple[Optional[dict], Optional[Tuple[dict, int]]]:
    """Parse a JSON object field that may arrive with varying quoting/escaping."""

    if rawValue is None:
        logging.warning('Missing JSON payload for field %s.', fieldName)
        return None, ({'error': 'Invalid JSON format for associated data'}, 400)

    candidates = []
    trimmedValue = rawValue.strip()
    candidates.append(rawValue)
    if trimmedValue != rawValue:
        candidates.append(trimmedValue)

    if trimmedValue and trimmedValue[0] == trimmedValue[-1] and trimmedValue[0] in {"'", '"'}:
        candidates.append(trimmedValue[1:-1])

    if '\\' in rawValue:
        try:
            unescapedValue = bytes(rawValue, 'utf-8').decode('unicode_escape')
            if unescapedValue != rawValue:
                candidates.append(unescapedValue)
        except UnicodeDecodeError:
            logging.debug('Failed to unicode-unescape field %s. Proceeding with originals.', fieldName)

    lastErrorMessage = None
    for candidate in candidates:
        valueToParse = candidate
        for _ in range(3):
            try:
                parsedValue = json.loads(valueToParse)
            except json.JSONDecodeError as error:
                lastErrorMessage = error.msg
                break

            if isinstance(parsedValue, dict):
                return parsedValue, None

            if isinstance(parsedValue, str):
                valueToParse = parsedValue.strip()
                continue

            logging.warning('JSON payload for %s must be an object, received %s.', fieldName, type(parsedValue).__name__)
            return None, ({'error': 'Invalid JSON format for associated data'}, 400)

    fallbackParsed = tryParseKeyValueObject(rawValue)
    if fallbackParsed is not None:
        logging.info('Parsed field %s using key/value fallback.', fieldName)
        return fallbackParsed, None

    if lastErrorMessage:
        logging.warning('Invalid JSON for field %s: %s', fieldName, lastErrorMessage)
    else:
        logging.warning('Invalid JSON for field %s.', fieldName)

    return None, ({'error': 'Invalid JSON format for associated data'}, 400)


def getClients() -> ClientBundle:
    global cachedClients  # pylint: disable=global-statement

    if cachedClients is not None:
        return cachedClients

    gcpProjectId = os.environ.get('GCP_PROJECT_ID')
    gcsBucketName = os.environ.get('GCS_BUCKET_NAME')
    kmsKeyRing = os.environ.get('KMS_KEY_RING')
    kmsKeyName = os.environ.get('KMS_KEY_NAME')
    kmsLocation = os.environ.get('KMS_LOCATION')

    missingConfig = [
        configName
        for configName, configValue in [
            ('GCP_PROJECT_ID', gcpProjectId),
            ('GCS_BUCKET_NAME', gcsBucketName),
            ('KMS_KEY_RING', kmsKeyRing),
            ('KMS_KEY_NAME', kmsKeyName),
            ('KMS_LOCATION', kmsLocation),
        ]
        if not configValue
    ]

    if missingConfig:
        raise MissingEnvironmentError(missingConfig)

    try:
        credentials = None
        if googleAuthDefault is not None:
            credentials, _ = googleAuthDefault(
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )

        storageClientKwargs: Dict[str, object] = {'project': gcpProjectId}
        if credentials is not None:
            storageClientKwargs['credentials'] = credentials
        storageClient = storage.Client(**storageClientKwargs)

        firestoreClientKwargs: Dict[str, object] = {'project': gcpProjectId}
        if credentials is not None:
            firestoreClientKwargs['credentials'] = credentials
        firestoreClient = firestore.Client(**firestoreClientKwargs)

        kmsClientKwargs: Dict[str, object] = {}
        if credentials is not None:
            kmsClientKwargs['credentials'] = credentials
        kmsClient = kms_v1.KeyManagementServiceClient(**kmsClientKwargs)
        kmsKeyPath = kmsClient.crypto_key_path(gcpProjectId, kmsLocation, kmsKeyRing, kmsKeyName)
    except Exception as error:  # pylint: disable=broad-except
        raise ClientInitializationError('Google Cloud clients', error) from error

    cachedClients = ClientBundle(
        storageClient=storageClient,
        firestoreClient=firestoreClient,
        kmsClient=kmsClient,
        kmsKeyPath=kmsKeyPath,
        gcsBucketName=gcsBucketName,
    )

    logging.info(
        "Initialized Google Cloud clients with Project: %s, Bucket: %s, KMS Key: %s",
        gcpProjectId,
        gcsBucketName,
        kmsKeyPath,
    )

    return cachedClients


def fetchClientsOrResponse() -> Tuple[Optional[ClientBundle], Optional[Tuple[dict, int]]]:
    try:
        return getClients(), None
    except MissingEnvironmentError as error:
        logging.error(
            "Missing one or more essential environment variables: %s",
            ', '.join(sorted(error.missingVariables)),
        )
        return None, (
            {
                'error': 'Missing environment configuration',
                'missingVariables': sorted(error.missingVariables),
            },
            503,
        )
    except ClientInitializationError as error:
        logging.error('Failed to initialize Google Cloud clients: %s', error.detail)
        return None, (
            {
                'error': 'Failed to initialize Google Cloud clients',
                'detail': error.detail,
            },
            500,
        )


def generateFetchToken() -> str:
    return secrets.token_urlsafe(32)


def normalizeTimestamp(value: Optional[datetime]) -> Optional[str]:
    if not value or not isinstance(value, datetime):
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat()


def parseIso8601Timestamp(rawTimestamp: object) -> Optional[datetime]:
    if not isinstance(rawTimestamp, str):
        return None

    trimmedTimestamp = rawTimestamp.strip()
    if not trimmedTimestamp:
        return None

    sanitizedTimestamp = trimmedTimestamp
    if sanitizedTimestamp.endswith('Z'):
        sanitizedTimestamp = sanitizedTimestamp[:-1] + '+00:00'

    try:
        parsedTimestamp = datetime.fromisoformat(sanitizedTimestamp)
    except ValueError:
        return None

    if parsedTimestamp.tzinfo is None:
        parsedTimestamp = parsedTimestamp.replace(tzinfo=timezone.utc)

    return parsedTimestamp.astimezone(timezone.utc)


@app.route('/upload', methods=['POST'])
def uploadFile():
    logging.info('Received request to /upload')
    try:
        clients, errorResponse = fetchClientsOrResponse()
        if errorResponse:
            return jsonify(errorResponse[0]), errorResponse[1]

        storageClient = clients.storageClient
        firestoreClient = clients.firestoreClient
        kmsClient = clients.kmsClient
        kmsKeyPath = clients.kmsKeyPath
        gcsBucketName = clients.gcsBucketName

        if 'file' not in request.files:
            logging.warning('No file part in the request.')
            return jsonify({'error': 'No file part'}), 400

        upload = request.files['file']
        if not upload.filename:
            logging.warning('No selected file.')
            return jsonify({'error': 'No selected file'}), 400

        unencryptedDataRaw = request.form.get('unencrypted_data', '{}')
        encryptedDataPayloadRaw = request.form.get('encrypted_data_payload', '{}')
        recipientId = request.form.get('recipient_id')
        productIdRaw = request.form.get('product_id')

        if not recipientId:
            logging.warning('Recipient ID is missing.')
            return jsonify({'error': 'Recipient ID is required'}), 400

        if not productIdRaw:
            logging.warning('Product ID is missing.')
            return jsonify({'error': 'Product ID is required'}), 400

        try:
            productUuid = uuid.UUID(str(productIdRaw))
        except (ValueError, AttributeError, TypeError):
            logging.warning('Invalid product ID provided: %s', productIdRaw)
            return jsonify({'error': 'Invalid product ID format'}), 400

        productId = str(productUuid)

        unencryptedData, errorResponse = parseJsonObjectField(unencryptedDataRaw, 'unencrypted_data')
        if errorResponse:
            return jsonify(errorResponse[0]), errorResponse[1]

        encryptedDataPayload, errorResponse = parseJsonObjectField(
            encryptedDataPayloadRaw, 'encrypted_data_payload'
        )
        if errorResponse:
            return jsonify(errorResponse[0]), errorResponse[1]

        fileId = str(uuid.uuid4())
        normalizedFilename = secure_filename(upload.filename)
        if not normalizedFilename:
            logging.warning('Invalid or empty filename provided.')
            return jsonify({'error': 'Invalid filename'}), 400

        _, fileExtension = os.path.splitext(normalizedFilename)
        fileExtension = fileExtension.lower()
        if fileExtension not in allowedUploadExtensions:
            logging.warning('Rejected file with unsupported extension: %s', fileExtension)
            return (
                jsonify(
                    {
                        'error': (
                            'Unsupported file type. Allowed extensions: '
                            + ', '.join(sorted(allowedUploadExtensions))
                        )
                    }
                ),
                400,
            )

        if upload.mimetype and upload.mimetype not in allowedUploadMimeTypes:
            logging.warning(
                'Rejected file due to unsupported MIME type: %s', upload.mimetype
            )
            return (
                jsonify(
                    {
                        'error': (
                            'Unsupported MIME type. Allowed types: '
                            + ', '.join(sorted(allowedUploadMimeTypes))
                        )
                    }
                ),
                400,
            )

        gcsObjectName = f"{recipientId}/{productId}_{normalizedFilename}"
        bucket = storageClient.bucket(gcsBucketName)
        blob = bucket.blob(gcsObjectName)

        blob.upload_from_file(upload)
        logging.info(
            'File %s uploaded to gs://%s/%s', normalizedFilename, gcsBucketName, gcsObjectName
        )

        try:
            encryptResponse = kmsClient.encrypt(
                request={
                    'name': kmsKeyPath,
                    'plaintext': json.dumps(encryptedDataPayload).encode('utf-8'),
                }
            )
            encryptedDataCipherText = encryptResponse.ciphertext.hex()
            logging.info('Sensitive data encrypted with KMS.')
        except GoogleAPICallError as error:
            logging.error('KMS encryption failed: %s', error)
            return jsonify({'error': f'KMS encryption failed: {error.message}'}), 500

        fetchToken = generateFetchToken()
        metadata = {
            'fileId': fileId,
            'originalFilename': normalizedFilename,
            'gcsPath': gcsObjectName,
            'encryptedData': encryptedDataCipherText,
            'unencryptedData': unencryptedData,
            'recipientId': recipientId,
            'productId': productId,
            'fetchToken': fetchToken,
            'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=fetchTokenTtlMinutes),
            'fetchTokenConsumed': False,
            'status': 'uploaded',
            'timestamp': firestore.SERVER_TIMESTAMP,
            'lastRequestTimestamp': None,
            'lastRequestFileName': normalizedFilename,
        }

        firestoreClient.collection(firestoreCollectionFiles).document(fileId).set(metadata)
        logging.info('Metadata for file %s stored in Firestore.', fileId)

        return jsonify(
            {
                'message': 'File uploaded successfully',
                'fileId': fileId,
                'productId': productId,
                'fetchToken': fetchToken,
            }
        ), 200

    except Exception:  # pylint: disable=broad-except
        logging.exception('An unexpected error occurred during file upload.')
        return jsonify({'error': 'Internal server error'}), 500


def pickLatestDocumentByTimestamp(documentSnapshots: List):
    latestSnapshot = None
    latestTimestamp = None
    for snapshot in documentSnapshots:
        metadata = snapshot.to_dict() or {}
        timestamp = metadata.get('timestamp')
        if isinstance(timestamp, datetime):
            candidateTimestamp = timestamp
        else:
            candidateTimestamp = None

        if latestSnapshot is None:
            latestSnapshot = snapshot
            latestTimestamp = candidateTimestamp
            continue

        if candidateTimestamp is None:
            continue

        if latestTimestamp is None or candidateTimestamp > latestTimestamp:
            latestSnapshot = snapshot
            latestTimestamp = candidateTimestamp

    return latestSnapshot


def buildHandshakeResponseMetadata(fileMetadata: dict) -> dict:
    metadataPayload = fileMetadata.get('unencryptedData')
    if isinstance(metadataPayload, dict):
        return metadataPayload
    if metadataPayload is None:
        return {}
    if isinstance(metadataPayload, str):
        return {'value': metadataPayload}
    return {}


@app.route('/products/<productId>/handshake', methods=['POST'])
def productHandshake(productId: str):
    logging.info('Received request to /products/%s/handshake', productId)
    try:
        clients, errorResponse = fetchClientsOrResponse()
        if errorResponse:
            return jsonify(errorResponse[0]), errorResponse[1]

        firestoreClient = clients.firestoreClient

        if not productId:
            logging.warning('Product ID is missing during handshake.')
            return jsonify({'error': 'Product ID is required'}), 400

        if not request.is_json:
            logging.warning('Handshake request must be JSON.')
            return jsonify({'error': 'Request must be JSON'}), 400

        payload = request.get_json()
        if not isinstance(payload, dict):
            logging.warning('Handshake payload is not a JSON object.')
            return jsonify({'error': 'Invalid JSON format: expected object'}), 400

        clientStatus = payload.get('status')
        if clientStatus not in {'hasFile', 'needsFile'}:
            logging.warning('Invalid handshake status received: %s', clientStatus)
            return jsonify({'error': 'Invalid status value'}), 400

        fileQuery = firestoreClient.collection(firestoreCollectionFiles).where(
            'productId', '==', productId
        )
        documentSnapshots = list(fileQuery.stream())

        if not documentSnapshots:
            logging.info('No files found in handshake for product %s', productId)
            return jsonify({'error': 'File not found for product'}), 404

        documentSnapshot = pickLatestDocumentByTimestamp(documentSnapshots)
        if documentSnapshot is None:
            logging.info('No valid document snapshots for product %s', productId)
            return jsonify({'error': 'File not found for product'}), 404

        fileMetadata = documentSnapshot.to_dict() or {}
        fetchToken = fileMetadata.get('fetchToken')
        fetchTokenConsumed = fileMetadata.get('fetchTokenConsumed')
        fetchTokenExpiry = fileMetadata.get('fetchTokenExpiry')
        originalFilename = fileMetadata.get('originalFilename')
        previousRequestTimestamp = normalizeTimestamp(
            fileMetadata.get('lastRequestTimestamp')
        )

        currentTime = datetime.now(timezone.utc)
        fetchMode = 'metadata' if clientStatus == 'hasFile' else 'full'

        if fetchMode == 'full':
            if fetchTokenConsumed:
                logging.warning(
                    'Fetch token already consumed for product %s during handshake.',
                    productId,
                )
                return jsonify({'error': 'Fetch token already used'}), 410

            if not fetchToken:
                logging.warning(
                    'Missing fetch token for product %s during handshake.', productId
                )
                return jsonify({'error': 'Fetch token missing'}), 422

            if isinstance(fetchTokenExpiry, datetime) and fetchTokenExpiry < currentTime:
                logging.warning(
                    'Fetch token expired for product %s during handshake.', productId
                )
                return jsonify({'error': 'Fetch token expired'}), 410

        handshakeStatus = (
            'handshake-metadata' if fetchMode == 'metadata' else 'handshake-download'
        )
        handshakeUpdatePayload = {
            'lastRequestTimestamp': currentTime,
            'lastRequestFileName': originalFilename,
            'status': handshakeStatus,
            'handshakeClientStatus': clientStatus,
        }

        if fetchMode == 'metadata':
            handshakeUpdatePayload.update(
                {
                    'fetchToken': DELETE_FIELD,
                    'fetchTokenExpiry': DELETE_FIELD,
                    'fetchTokenConsumed': True,
                    'fetchTokenConsumedTimestamp': currentTime,
                }
            )

        firestoreClient.collection(firestoreCollectionFiles).document(
            documentSnapshot.id
        ).update(handshakeUpdatePayload)

        responsePayload = {
            'productId': productId,
            'fileId': documentSnapshot.id,
            'clientStatus': clientStatus,
            'decision': fetchMode,
            'fetchMode': fetchMode,
            'fetchToken': fetchToken,
            'originalFilename': originalFilename,
            'lastRequestTimestamp': currentTime.isoformat(),
            'previousRequestTimestamp': previousRequestTimestamp,
            'lastRequestFileName': originalFilename,
            'metadata': buildHandshakeResponseMetadata(fileMetadata),
            'downloadRequired': fetchMode == 'full',
        }

        if isinstance(fetchTokenExpiry, datetime):
            responsePayload['fetchTokenExpiry'] = fetchTokenExpiry.isoformat()

        return jsonify(responsePayload), 200

    except Exception:  # pylint: disable=broad-except
        logging.exception('Unexpected error during product handshake.')
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/products/<productId>/status', methods=['POST'])
def productStatusUpdate(productId: str):
    logging.info('Received request to /products/%s/status', productId)
    try:
        clients, errorResponse = fetchClientsOrResponse()
        if errorResponse:
            return jsonify(errorResponse[0]), errorResponse[1]

        firestoreClient = clients.firestoreClient

        if not productId:
            logging.warning('Product ID is missing during status update.')
            return jsonify({'error': 'Product ID is required'}), 400

        if not request.is_json:
            logging.warning('Product status update must be JSON.')
            return jsonify({'error': 'Request must be JSON'}), 400

        try:
            payload = request.get_json()
        except Exception as error:  # pylint: disable=broad-except
            logging.warning('Failed to parse product status JSON payload: %s', error)
            return jsonify({'error': 'Invalid JSON payload'}), 400

        if not isinstance(payload, dict):
            logging.warning('Product status payload is not a JSON object.')
            return jsonify({'error': 'Invalid JSON format: expected object'}), 400

        requiredFields = ['productId', 'requestedMode', 'success', 'fileName', 'lastRequestedAt']
        missingFields = [field for field in requiredFields if field not in payload]
        if missingFields:
            logging.warning('Missing required fields in product status update: %s', ', '.join(missingFields))
            return (
                jsonify({'error': f"Missing required field(s): {', '.join(missingFields)}"}),
                400,
            )

        bodyProductId = payload.get('productId')
        if not isinstance(bodyProductId, str) or not bodyProductId.strip():
            logging.warning('Invalid productId value in status payload: %s', bodyProductId)
            return jsonify({'error': 'Invalid productId value'}), 400

        if bodyProductId != productId:
            logging.warning('Product ID mismatch between path and payload: %s vs %s', productId, bodyProductId)
            return jsonify({'error': 'Product ID mismatch'}), 409

        requestedMode = payload.get('requestedMode')
        if requestedMode not in {'full', 'metadata'}:
            logging.warning('Invalid requestedMode value in status payload: %s', requestedMode)
            return jsonify({'error': 'Invalid requestedMode value'}), 400

        successValue = payload.get('success')
        if not isinstance(successValue, bool):
            logging.warning('Invalid success value type in status payload for product %s', productId)
            return jsonify({'error': 'success must be a boolean'}), 400

        fileName = payload.get('fileName')
        if not isinstance(fileName, str) or not fileName.strip():
            logging.warning('Invalid fileName value in status payload for product %s', productId)
            return jsonify({'error': 'Invalid fileName value'}), 400

        parsedLastRequestedAt = parseIso8601Timestamp(payload.get('lastRequestedAt'))
        if parsedLastRequestedAt is None:
            logging.warning('Invalid lastRequestedAt timestamp in status payload for product %s', productId)
            return jsonify({'error': 'Invalid lastRequestedAt timestamp'}), 400

        recipientId = payload.get('recipientId')

        fileQuery = firestoreClient.collection(firestoreCollectionFiles).where('productId', '==', productId)
        documentSnapshots = list(fileQuery.stream())

        if not documentSnapshots:
            logging.info('No files found when recording status for product %s', productId)
            return jsonify({'error': 'File not found for product'}), 404

        documentSnapshot = pickLatestDocumentByTimestamp(documentSnapshots)
        if documentSnapshot is None:
            logging.info('No valid file snapshots found when recording status for product %s', productId)
            return jsonify({'error': 'File not found for product'}), 404

        fileMetadata = documentSnapshot.to_dict() or {}

        fetchTokenData: Dict[str, object] = {
            'fetchToken': fileMetadata.get('fetchToken'),
            'fetchTokenConsumed': fileMetadata.get('fetchTokenConsumed'),
        }

        fetchTokenExpiryNormalized = normalizeTimestamp(fileMetadata.get('fetchTokenExpiry'))
        if fetchTokenExpiryNormalized:
            fetchTokenData['fetchTokenExpiry'] = fetchTokenExpiryNormalized

        fetchTokenConsumedTimestamp = normalizeTimestamp(fileMetadata.get('fetchTokenConsumedTimestamp'))
        if fetchTokenConsumedTimestamp:
            fetchTokenData['fetchTokenConsumedTimestamp'] = fetchTokenConsumedTimestamp

        statusRecord: Dict[str, object] = {
            'productId': productId,
            'fileId': documentSnapshot.id,
            'requestedMode': requestedMode,
            'success': successValue,
            'fileName': fileName,
            'lastRequestedAt': parsedLastRequestedAt.isoformat(),
            'receivedAt': firestore.SERVER_TIMESTAMP,
            'payload': dict(payload),
            'fetchTokenData': fetchTokenData,
        }

        if isinstance(recipientId, str):
            sanitizedRecipientId = recipientId.strip()
            if sanitizedRecipientId:
                statusRecord['recipientId'] = sanitizedRecipientId

        printerDetailsRaw = payload.get('printerDetails')
        printerDetailsSource: Optional[Dict[str, object]] = None
        if isinstance(printerDetailsRaw, dict):
            printerDetailsSource = printerDetailsRaw
        elif isinstance(printerDetailsRaw, list):
            for detailCandidate in reversed(printerDetailsRaw):
                if isinstance(detailCandidate, dict):
                    printerDetailsSource = detailCandidate
                    break

        if printerDetailsSource:
            printerDetailFieldMap = {
                'serialNumber': 'printerSerial',
                'ipAddress': 'printerIp',
                'nickname': 'printerNickname',
                'brand': 'printerBrand',
            }
            for sourceKey, targetKey in printerDetailFieldMap.items():
                detailValue = printerDetailsSource.get(sourceKey)
                if detailValue is not None:
                    statusRecord[targetKey] = detailValue

        printerEventRaw = payload.get('printerEvent')
        printerEventSource: Optional[Dict[str, object]] = None
        if isinstance(printerEventRaw, dict):
            printerEventSource = printerEventRaw
        elif isinstance(printerEventRaw, list):
            for eventCandidate in reversed(printerEventRaw):
                if isinstance(eventCandidate, dict):
                    printerEventSource = eventCandidate
                    break

        if printerEventSource:
            eventKeyOptions = ('eventType', 'event', 'status', 'type')
            eventValue = next(
                (printerEventSource.get(key) for key in eventKeyOptions if printerEventSource.get(key) is not None),
                None,
            )
            if eventValue is not None:
                statusRecord['statusEvent'] = eventValue

            messageKeyOptions = ('message', 'detail', 'statusMessage', 'description', 'info')
            messageValue = next(
                (
                    printerEventSource.get(key)
                    for key in messageKeyOptions
                    if printerEventSource.get(key) is not None
                ),
                None,
            )
            if messageValue is not None:
                statusRecord['statusMessage'] = messageValue

        fileTimestamp = normalizeTimestamp(fileMetadata.get('timestamp'))
        if fileTimestamp:
            statusRecord['fileTimestamp'] = fileTimestamp

        currentFileStatus = fileMetadata.get('status')
        if currentFileStatus is not None:
            statusRecord['fileStatus'] = currentFileStatus

        firestoreClient.collection(firestoreCollectionPrinterStatus).add(statusRecord)
        logging.info('Stored product status update for %s with file %s', productId, documentSnapshot.id)

        return jsonify({'message': 'Product status recorded'}), 200

    except Exception:  # pylint: disable=broad-except
        logging.exception('Unexpected error while recording product status update for %s', productId)
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/fetch/<fetchToken>', methods=['GET'])
def fetchFile(fetchToken: str):
    logging.info('Received request to /fetch/%s', fetchToken)
    try:
        clients, errorResponse = fetchClientsOrResponse()
        if errorResponse:
            return jsonify(errorResponse[0]), errorResponse[1]

        storageClient = clients.storageClient
        firestoreClient = clients.firestoreClient
        kmsClient = clients.kmsClient
        kmsKeyPath = clients.kmsKeyPath
        gcsBucketName = clients.gcsBucketName

        if not fetchToken:
            logging.warning('Fetch token is missing.')
            return jsonify({'error': 'Fetch token is required'}), 400

        fileQuery = (
            firestoreClient.collection(firestoreCollectionFiles)
            .where('fetchToken', '==', fetchToken)
            .limit(1)
        )
        fileDocuments = list(fileQuery.stream())

        if not fileDocuments:
            logging.warning('No file found for fetch token: %s', fetchToken)
            return jsonify({'error': 'File not found or token invalid/expired'}), 404

        documentSnapshot = fileDocuments[0]
        fileMetadata = documentSnapshot.to_dict() or {}

        if fileMetadata.get('fetchTokenConsumed'):
            logging.warning('Fetch token already consumed for file %s', documentSnapshot.id)
            return jsonify({'error': 'Fetch token already used'}), 410

        fetchTokenExpiry = fileMetadata.get('fetchTokenExpiry')
        currentTime = datetime.now(timezone.utc)
        if fetchTokenExpiry and fetchTokenExpiry < currentTime:
            logging.warning('Fetch token expired for file %s', documentSnapshot.id)
            return jsonify({'error': 'Fetch token expired'}), 410

        unencryptedData = fileMetadata.get('unencryptedData')
        encryptedDataHex = fileMetadata.get('encryptedData')
        decryptedData = {}

        if encryptedDataHex:
            try:
                encryptedDataCipherText = bytes.fromhex(encryptedDataHex)
            except ValueError:
                logging.warning(
                    'Invalid encryptedData stored for file %s', documentSnapshot.id
                )
                return (
                    jsonify({'error': 'Stored encrypted data is invalid'}),
                    422,
                )

            try:
                decryptResponse = kmsClient.decrypt(
                    request={'name': kmsKeyPath, 'ciphertext': encryptedDataCipherText}
                )
                try:
                    decryptedData = json.loads(decryptResponse.plaintext.decode('utf-8'))
                except json.JSONDecodeError:
                    logging.warning(
                        'Decrypted metadata is not valid JSON for file %s',
                        documentSnapshot.id,
                    )
                    return jsonify({'error': 'Decrypted metadata is invalid JSON'}), 422
                logging.info('Sensitive data decrypted with KMS.')
            except GoogleAPICallError as error:
                logging.error('KMS decryption failed: %s', error)
                return jsonify({'error': f'KMS decryption failed: {error.message}'}), 500
        else:
            if unencryptedData is None:
                logging.warning(
                    'Missing encrypted and unencrypted data for file %s',
                    documentSnapshot.id,
                )
                return jsonify({'error': 'File metadata is incomplete'}), 422

            decryptedData = unencryptedData
            logging.info(
                'No encrypted data found for file %s; using stored unencrypted metadata.',
                documentSnapshot.id,
            )

        requestArgs = getattr(request, 'args', {}) or {}
        fetchMode = str(requestArgs.get('mode', 'full')).lower()
        metadataOnly = fetchMode == 'metadata'

        signedUrl = None
        gcsPath = fileMetadata.get('gcsPath')
        if not metadataOnly:
            bucket = storageClient.bucket(gcsBucketName)
            if not gcsPath:
                logging.warning(
                    'Missing gcsPath in metadata for file %s', documentSnapshot.id
                )
                return (
                    jsonify({'error': 'File metadata is incomplete: missing gcsPath'}),
                    422,
                )

            blob = bucket.blob(gcsPath)

            try:
                credentials = getattr(storageClient, '_credentials', None)
                if credentials is not None:
                    signBytesMethod = getattr(credentials, 'sign_bytes', None)
                    if callable(signBytesMethod):
                        signedUrl = blob.generate_signed_url(
                            version='v4',
                            expiration=timedelta(minutes=15),
                            method='GET',
                        )
                    else:
                        if Request is None:
                            raise ImportError('google.auth Request is required for IAM signing')
                        requestAdapter = Request()
                        scopedCredentials = credentials
                        withScopesMethod = getattr(credentials, 'with_scopes_if_required', None)
                        if callable(withScopesMethod):
                            scopedCredentials = withScopesMethod(
                                ['https://www.googleapis.com/auth/cloud-platform']
                            )
                            if scopedCredentials is None:
                                scopedCredentials = credentials
                        credentials = scopedCredentials
                        credentials.refresh(requestAdapter)
                        accessToken = getattr(credentials, 'token', None)
                        if not accessToken:
                            raise AttributeError(
                                'Credentials missing access token required for IAM signing'
                            )
                        serviceAccountEmail = getattr(
                            credentials, 'service_account_email', None
                        )
                        if not serviceAccountEmail:
                            raise AttributeError(
                                'Credentials missing service_account_email required for IAM signing'
                            )
                        signedUrl = blob.generate_signed_url(
                            version='v4',
                            expiration=timedelta(minutes=15),
                            method='GET',
                            service_account_email=serviceAccountEmail,
                            access_token=accessToken,
                        )

                if signedUrl is None:
                    signedUrl = blob.generate_signed_url(
                        version='v4',
                        expiration=timedelta(minutes=15),
                        method='GET',
                    )
            except (AttributeError, ImportError, TypeError, GoogleAuthError) as error:
                logging.exception(
                    'Service account is missing a signing key required for signed URL generation: %s',
                    error,
                )
                return (
                    jsonify(
                        {
                            'error': 'Service account lacks a signing capability required for signed URL generation',
                            'detail': str(error),
                        }
                    ),
                    503,
                )
            except (Forbidden, PermissionDenied, Unauthorized) as error:
                missingPermissions = ['storage.objects.sign', 'iam.serviceAccounts.signBlob']
                logging.error(
                    'Missing IAM permissions for signed URL generation: %s',
                    error,
                )
                return (
                    jsonify(
                        {
                            'error': 'Missing required IAM permissions to generate signed URL',
                            'missingPermissions': missingPermissions,
                            'detail': str(error),
                        }
                    ),
                    403,
                )
            except GoogleAPICallError as error:
                logging.exception(
                    'Storage API call failed during signed URL generation: %s',
                    error,
                )
                errorDetail = getattr(error, 'message', str(error))
                return (
                    jsonify(
                        {
                            'error': 'Storage service temporarily unavailable for signed URL generation',
                            'detail': errorDetail,
                        }
                    ),
                    503,
                )
            logging.info('Generated signed URL for gs://%s/%s', gcsBucketName, gcsPath)

        requestTimestamp = datetime.now(timezone.utc)
        updatePayload = {
            'lastRequestTimestamp': requestTimestamp,
            'lastRequestFileName': fileMetadata.get('originalFilename'),
            'lastFetchMode': fetchMode,
        }

        if metadataOnly:
            updatePayload.update(
                {
                    'status': 'metadata-fetched',
                    'metadataFetchTimestamp': firestore.SERVER_TIMESTAMP,
                    'fetchToken': DELETE_FIELD,
                    'fetchTokenExpiry': DELETE_FIELD,
                    'fetchTokenConsumed': True,
                    'fetchTokenConsumedTimestamp': firestore.SERVER_TIMESTAMP,
                }
            )
        else:
            updatePayload.update(
                {
                    'status': 'fetched',
                    'fetchedTimestamp': firestore.SERVER_TIMESTAMP,
                    'fetchToken': DELETE_FIELD,
                    'fetchTokenExpiry': DELETE_FIELD,
                    'fetchTokenConsumed': True,
                    'fetchTokenConsumedTimestamp': firestore.SERVER_TIMESTAMP,
                }
            )

        firestoreClient.collection(firestoreCollectionFiles).document(
            documentSnapshot.id
        ).update(updatePayload)
        logging.info('Updated status for file %s to %s.', documentSnapshot.id, updatePayload['status'])

        responsePayload = {
            'message': (
                'Metadata retrieved successfully'
                if metadataOnly
                else 'File and data retrieved successfully'
            ),
            'unencryptedData': unencryptedData if isinstance(unencryptedData, dict) else unencryptedData or {},
            'decryptedData': decryptedData,
            'fetchMode': fetchMode,
            'lastRequestTimestamp': requestTimestamp.isoformat(),
            'lastRequestFileName': fileMetadata.get('originalFilename'),
        }

        if not metadataOnly:
            responsePayload['signedUrl'] = signedUrl
            responsePayload['gcsPath'] = gcsPath

        return jsonify(responsePayload), 200

    except Exception:  # pylint: disable=broad-except
        logging.exception('An unexpected error occurred during file fetch.')
        return jsonify({'error': 'Internal server error'}), 500


def buildPendingFileList(
    firestoreClient: firestore.Client, recipientId: str
) -> Tuple[List[Dict[str, Optional[str]]], List[str]]:
    pendingFiles: List[Dict[str, Optional[str]]] = []
    skippedFiles: List[str] = []
    currentTime = datetime.now(timezone.utc)

    fileQuery = (
        firestoreClient.collection(firestoreCollectionFiles)
        .where('recipientId', '==', recipientId)
        .where('fetchTokenConsumed', '==', False)
    )

    for documentSnapshot in fileQuery.stream():
        metadata = documentSnapshot.to_dict() or {}
        fetchToken = metadata.get('fetchToken')
        if not fetchToken:
            skippedFiles.append(documentSnapshot.id)
            continue

        fetchTokenExpiry = metadata.get('fetchTokenExpiry')
        if isinstance(fetchTokenExpiry, datetime) and fetchTokenExpiry < currentTime:
            skippedFiles.append(documentSnapshot.id)
            continue

        pendingFiles.append(
            {
                'fileId': documentSnapshot.id,
                'originalFilename': metadata.get('originalFilename'),
                'productId': metadata.get('productId'),
                'fetchToken': fetchToken,
                'fetchTokenExpiry': normalizeTimestamp(fetchTokenExpiry),
                'status': metadata.get('status'),
                'uploadedAt': normalizeTimestamp(metadata.get('timestamp')),
            }
        )

    pendingFiles.sort(key=lambda item: item.get('uploadedAt') or '')

    return pendingFiles, skippedFiles


@app.route('/recipients/<recipientId>/pending', methods=['GET'])
def listPendingFiles(recipientId: str):
    logging.info('Received request to /recipients/%s/pending', recipientId)
    try:
        clients, errorResponse = fetchClientsOrResponse()
        if errorResponse:
            return jsonify(errorResponse[0]), errorResponse[1]

        firestoreClient = clients.firestoreClient

        if not recipientId:
            logging.warning('Recipient ID is missing when listing pending files.')
            return jsonify({'error': 'Recipient ID is required'}), 400

        pendingFiles, skippedFiles = buildPendingFileList(firestoreClient, recipientId)

        responsePayload = {
            'recipientId': recipientId,
            'pendingFiles': pendingFiles,
        }
        if skippedFiles:
            responsePayload['skippedFiles'] = skippedFiles

        return jsonify(responsePayload), 200

    except Exception:  # pylint: disable=broad-except
        logging.exception('An unexpected error occurred while listing pending files.')
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/printer-status', methods=['POST'])
def printerStatusUpdate():
    logging.info('Received request to /printer-status')
    try:
        clients, errorResponse = fetchClientsOrResponse()
        if errorResponse:
            return jsonify(errorResponse[0]), errorResponse[1]

        firestoreClient = clients.firestoreClient

        apiKey = request.headers.get('X-API-Key')
        if not apiKey or apiKey not in validPrinterApiKeys:
            logging.warning('Unauthorized access attempt to /printer-status with API Key: %s', apiKey)
            return jsonify({'error': 'Unauthorized: Invalid API Key'}), 401

        if not request.is_json:
            logging.warning('Request content type is not JSON.')
            return jsonify({'error': 'Request must be JSON'}), 400

        statusData = request.get_json()
        if not isinstance(statusData, dict):
            logging.warning('JSON payload is not a dictionary.')
            return jsonify({'error': 'Invalid JSON format: expected a dictionary'}), 400

        recipientId = statusData.get('recipientId')
        if recipientId is not None:
            if not isinstance(recipientId, str):
                logging.warning(
                    'Invalid recipientId type in printer status update: %s',
                    type(recipientId).__name__,
                )
                return jsonify({'error': 'recipientId must be a non-empty string'}), 400
            sanitizedRecipientId = recipientId.strip()
            if not sanitizedRecipientId:
                logging.warning('Empty recipientId provided in printer status update.')
                return jsonify({'error': 'recipientId must be a non-empty string'}), 400
            statusData['recipientId'] = sanitizedRecipientId

        requiredFields = [
            'printerIp',
            'publicKey',
            'accessCode',
            'printerSerial',
            'objectName',
            'useAms',
            'printJobId',
            'productName',
            'platesRequested',
            'status',
            'jobProgress',
            'materialLevel',
        ]
        for field in requiredFields:
            if field not in statusData:
                logging.warning('Missing required field in printer status update: %s', field)
                return jsonify({'error': f'Missing required field: {field}'}), 400

        statusData['timestamp'] = firestore.SERVER_TIMESTAMP

        firestoreClient.collection(firestoreCollectionPrinterStatus).add(statusData)
        logging.info(
            'Printer status update received and stored for printerSerial: %s',
            statusData.get('printerSerial'),
        )

        return jsonify({'message': 'Printer status updated successfully'}), 200

    except Exception:  # pylint: disable=broad-except
        logging.exception('An unexpected error occurred during printer status update.')
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/', methods=['GET'])
def healthCheck():
    return jsonify({'status': 'ok', 'message': 'Cloud server is running!'}), 200


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=port)
