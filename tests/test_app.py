import json
from io import BytesIO


def testUploadFileStoresMetadataAndReturnsToken(appWithFakes):
    flaskApp = appWithFakes
    testClient = flaskApp.test_client()

    uploadResponse = testClient.post(
        "/upload",
        data={
            "file": (BytesIO(b"demo-bytes"), "demo.gcode"),
            "unencrypted_data": json.dumps({"printJob": "demo"}),
            "encrypted_data_payload": json.dumps({"secret": "1234"}),
            "recipient_id": "user-123",
        },
        content_type="multipart/form-data",
    )

    assert uploadResponse.status_code == 200
    responsePayload = uploadResponse.get_json()
    assert responsePayload["message"] == "File uploaded successfully"
    assert "fetchToken" in responsePayload

    fakeFirestoreClient = flaskApp.config["fakeFirestoreClient"]
    storedMetadata = fakeFirestoreClient.fakeStore[("files", responsePayload["fileId"])]
    assert storedMetadata["status"] == "uploaded"
    assert storedMetadata["unencryptedData"] == {"printJob": "demo"}
    assert isinstance(storedMetadata["encryptedData"], str)

    fakeStorageClient = flaskApp.config["fakeStorageClient"]
    assert fakeStorageClient.createdBlobs


def testUploadFileRejectsInvalidJson(appWithFakes):
    testClient = appWithFakes.test_client()

    uploadResponse = testClient.post(
        "/upload",
        data={
            "file": (BytesIO(b"demo-bytes"), "demo.gcode"),
            "unencrypted_data": "not-json",
            "encrypted_data_payload": json.dumps({"secret": "1234"}),
            "recipient_id": "user-123",
        },
        content_type="multipart/form-data",
    )

    assert uploadResponse.status_code == 400
    responsePayload = uploadResponse.get_json()
    assert responsePayload["error"] == "Invalid JSON format for associated data"


def testFetchFileReturnsSignedUrlAndDecryptsData(appWithFakes):
    flaskApp = appWithFakes
    testClient = flaskApp.test_client()

    uploadResponse = testClient.post(
        "/upload",
        data={
            "file": (BytesIO(b"demo-bytes"), "demo.gcode"),
            "unencrypted_data": json.dumps({"printJob": "demo"}),
            "encrypted_data_payload": json.dumps({"secret": "1234"}),
            "recipient_id": "user-123",
        },
        content_type="multipart/form-data",
    )
    fetchToken = uploadResponse.get_json()["fetchToken"]

    fetchResponse = testClient.get(f"/fetch/{fetchToken}")
    assert fetchResponse.status_code == 200
    fetchPayload = fetchResponse.get_json()
    assert fetchPayload["message"] == "File and data retrieved successfully"
    assert fetchPayload["unencryptedData"] == {"printJob": "demo"}
    assert fetchPayload["decryptedData"] == {"secret": "1234"}
    assert fetchPayload["signedUrl"].startswith("https://storage.example/")

    fakeFirestoreClient = flaskApp.config["fakeFirestoreClient"]
    storedMetadata = fakeFirestoreClient.fakeStore[("files", uploadResponse.get_json()["fileId"])]
    assert storedMetadata["status"] == "fetched"


def testPrinterStatusRejectsMissingApiKey(appWithFakes):
    testClient = appWithFakes.test_client()

    response = testClient.post(
        "/printer-status",
        json={"printerIp": "1.2.3.4"},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "Unauthorized: Invalid API Key"


def testPrinterStatusStoresPayload(appWithFakes):
    flaskApp = appWithFakes
    testClient = flaskApp.test_client()

    statusPayload = {
        "printerIp": "1.2.3.4",
        "publicKey": "pub",
        "accessCode": "code",
        "printerSerial": "serial",
        "objectName": "object",
        "useAms": True,
        "printJobId": "job-1",
        "productName": "printer",
        "platesRequested": 1,
        "status": "printing",
        "jobProgress": 12.5,
        "materialLevel": {"filament1": 80},
    }

    response = testClient.post(
        "/printer-status",
        json=statusPayload,
        headers={"X-API-Key": "printerKey"},
    )

    assert response.status_code == 200
    assert response.get_json()["message"] == "Printer status updated successfully"

    fakeFirestoreClient = flaskApp.config["fakeFirestoreClient"]
    storedDocuments = [
        data
        for (collectionName, _docId), data in fakeFirestoreClient.fakeStore.items()
        if collectionName == "printer_status_updates"
    ]
    assert storedDocuments
    storedDocument = storedDocuments[0]
    for key, value in statusPayload.items():
        assert storedDocument[key] == value
    assert storedDocument["timestamp"] == "server-timestamp"
