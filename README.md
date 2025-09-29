codex & cloud 


Project ID=print-pipe-demo
GCS_BUCKET_NAME=3mf-gcode-container
KMS_KEY_RING=my-printer-keyring
KMS_KEY_NAME=printer-data-key
KMS_LOCATION=europe-west1
FIRESTORE_COLLECTION_FILES=print_jobs
FIRESTORE_COLLECTION_PRINTER_STATUS=printer_telemetry
SECRET_MANAGER_API_KEYS_PATH=projects/934564650450/secrets/printer-api-keys/versions/latest


SECRET_MANAGER_API_KEYS_PATH keys=
1ORJkv4IZtQjYIniGFX8fr340VreiBhK1XNcDZ3GVlaNSPSCkm6EIZy4m6XOJDF0XAPLcELuZSQnEHxvBMqhD9b5q5Klf0QE9fwih9TOgC2K643cOrhOPZJMVwb9BV7i5Q7R8u8mxPutdWz0RVXP7w

c3Lr1YyProjUnzf2GeG8MeGYb0UWNt5jnZLd6Svk7DvysymtwkcJatQC4xlsdK9Cy3h4nFkEJmAXBib99tE5N7Ake2OO7rzZGhQSnGcXjhcYu1YOd7rwLKkHecqU8m4bFBjY9CBztbFRsRT883DFi7

curl.exe -X POST "https://printer-backend-934564650450.europe-west1.run.app/upload" `
  -F "file=@C:\Users\andre\Downloads\Cube.3mf" `
  --form-string recipient_id=user-123 `
  --form-string 'unencrypted_data={"printJob":"demo"}' `
  --form-string 'encrypted_data_payload={"secret":"1234"}'

  curl.exe -X POST "https://printer-backend-934564650450.europe-west1.run.app/upload" `
  -F "file=@C:\Users\508484\Downloads\googleting.gcode.3mf" `
  --form-string recipient_id=user-123 `
  --form-string 'unencrypted_data={"printJob":"demo"}' `
  --form-string 'encrypted_data_payload={"secret":"1234"}'

