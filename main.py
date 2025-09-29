import json
import logging
import os
import secrets
import uuid
from datetime import timedelta

from flask import Flask, jsonify, request
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import firestore, kms_v1, storage


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)


gcpProjectId = os.environ.get('GCP_PROJECT_ID')
gcsBucketName = os.environ.get('GCS_BUCKET_NAME')
kmsKeyRing = os.environ.get('KMS_KEY_RING')
kmsKeyName = os.environ.get('KMS_KEY_NAME')
kmsLocation = os.environ.get('KMS_LOCATION')
firestoreCollectionFiles = os.environ.get('FIRESTORE_COLLECTION_FILES', 'files')
firestoreCollectionPrinterStatus = os.environ.get('FIRESTORE_COLLECTION_PRINTER_STATUS', 'printer_status_updates')
apiKeysPrinterStatusStr = os.environ.get('API_KEYS_PRINTER_STATUS', '')
validPrinterApiKeys = {apiKey.strip() for apiKey in apiKeysPrinterStatusStr.split(',') if apiKey.strip()}
port = int(os.environ.get('PORT', '8080'))

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
    logging.error(
        "Missing one or more essential environment variables: %s",
        ', '.join(missingConfig),
    )
    raise RuntimeError('Missing essential environment configuration')

storageClient = storage.Client(project=gcpProjectId)
firestoreClient = firestore.Client(project=gcpProjectId)
kmsClient = kms_v1.KeyManagementServiceClient()
kmsKeyPath = kmsClient.crypto_key_path(gcpProjectId, kmsLocation, kmsKeyRing, kmsKeyName)

logging.info(
    "Initialized Google Cloud clients with Project: %s, Bucket: %s, KMS Key: %s",
    gcpProjectId,
    gcsBucketName,
    kmsKeyPath,
)


def generateFetchToken() -> str:
    return secrets.token_urlsafe(32)


@app.route('/upload', methods=['POST'])
def uploadFile():
    logging.info('Received request to /upload')
    try:
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

        try:
            unencryptedData = json.loads(unencryptedDataRaw)
            encryptedDataPayload = json.loads(encryptedDataPayloadRaw)
        except json.JSONDecodeError:
            logging.warning('Invalid JSON for unencrypted_data or encrypted_data_payload.')
            return jsonify({'error': 'Invalid JSON format for associated data'}), 400

        fileId = str(uuid.uuid4())
        originalFilename = upload.filename
        gcsObjectName = f"{recipientId}/{fileId}_{originalFilename}"
        bucket = storageClient.bucket(gcsBucketName)
        blob = bucket.blob(gcsObjectName)

        blob.upload_from_file(upload)
        logging.info('File %s uploaded to gs://%s/%s', originalFilename, gcsBucketName, gcsObjectName)

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
            'originalFilename': originalFilename,
            'gcsPath': gcsObjectName,
            'encryptedData': encryptedDataCipherText,
            'unencryptedData': unencryptedData,
            'recipientId': recipientId,
            'fetchToken': fetchToken,
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

        encryptedDataCipherText = bytes.fromhex(fileMetadata['encryptedData'])
        try:
            decryptResponse = kmsClient.decrypt(
                request={'name': kmsKeyPath, 'ciphertext': encryptedDataCipherText}
            )
            decryptedData = json.loads(decryptResponse.plaintext.decode('utf-8'))
            logging.info('Sensitive data decrypted with KMS.')
        except GoogleAPICallError as error:
            logging.error('KMS decryption failed: %s', error)
            return jsonify({'error': f'KMS decryption failed: {error.message}'}), 500

        bucket = storageClient.bucket(gcsBucketName)
        blob = bucket.blob(fileMetadata['gcsPath'])

        signedUrl = blob.generate_signed_url(
            version='v4',
            expiration=timedelta(minutes=15),
            method='GET',
        )
        logging.info('Generated signed URL for gs://%s/%s', gcsBucketName, fileMetadata['gcsPath'])

        firestoreClient.collection(firestoreCollectionFiles).document(documentSnapshot.id).update(
            {'status': 'fetched', 'fetchedTimestamp': firestore.SERVER_TIMESTAMP}
        )
        logging.info('Updated status for file %s to fetched.', documentSnapshot.id)

        return jsonify(
            {
                'message': 'File and data retrieved successfully',
                'signedUrl': signedUrl,
                'unencryptedData': fileMetadata.get('unencryptedData', {}),
                'decryptedData': decryptedData,
            }
        ), 200

    except Exception:  # pylint: disable=broad-except
        logging.exception('An unexpected error occurred during file fetch.')
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/printer-status', methods=['POST'])
def printerStatusUpdate():
    logging.info('Received request to /printer-status')
    try:
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
