import json
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, request
from google.api_core.exceptions import (
    Forbidden,
    GoogleAPICallError,
    PermissionDenied,
    Unauthorized,
)
from google.auth.exceptions import GoogleAuthError
from google.cloud import firestore, kms_v1, storage
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
apiKeysPrinterStatusStr = os.environ.get('API_KEYS_PRINTER_STATUS', '')
validPrinterApiKeys = {apiKey.strip() for apiKey in apiKeysPrinterStatusStr.split(',') if apiKey.strip()}
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
        storageClient = storage.Client(project=gcpProjectId)
        firestoreClient = firestore.Client(project=gcpProjectId)
        kmsClient = kms_v1.KeyManagementServiceClient()
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

        if not recipientId:
            logging.warning('Recipient ID is missing.')
            return jsonify({'error': 'Recipient ID is required'}), 400

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

        gcsObjectName = f"{recipientId}/{fileId}_{normalizedFilename}"
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
            'fetchToken': fetchToken,
            'fetchTokenExpiry': datetime.now(timezone.utc) + timedelta(minutes=fetchTokenTtlMinutes),
            'fetchTokenConsumed': False,
            'status': 'uploaded',
            'timestamp': firestore.SERVER_TIMESTAMP,
        }

        firestoreClient.collection(firestoreCollectionFiles).document(fileId).set(metadata)
        logging.info('Metadata for file %s stored in Firestore.', fileId)

        return jsonify(
            {
                'message': 'File uploaded successfully',
                'fileId': fileId,
                'fetchToken': fetchToken,
            }
        ), 200

    except Exception:  # pylint: disable=broad-except
        logging.exception('An unexpected error occurred during file upload.')
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

        bucket = storageClient.bucket(gcsBucketName)
        gcsPath = fileMetadata.get('gcsPath')
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
            signedUrl = blob.generate_signed_url(
                version='v4',
                expiration=timedelta(minutes=15),
                method='GET',
            )
        except (AttributeError, TypeError, GoogleAuthError) as error:
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

        firestoreClient.collection(firestoreCollectionFiles).document(documentSnapshot.id).update(
            {
                'status': 'fetched',
                'fetchedTimestamp': firestore.SERVER_TIMESTAMP,
                'fetchToken': DELETE_FIELD,
                'fetchTokenExpiry': DELETE_FIELD,
                'fetchTokenConsumed': True,
                'fetchTokenConsumedTimestamp': firestore.SERVER_TIMESTAMP,
            }
        )
        logging.info('Updated status for file %s to fetched.', documentSnapshot.id)

        return jsonify(
            {
                'message': 'File and data retrieved successfully',
                'signedUrl': signedUrl,
                'unencryptedData': unencryptedData if isinstance(unencryptedData, dict) else unencryptedData or {},
                'decryptedData': decryptedData,
            }
        ), 200

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
