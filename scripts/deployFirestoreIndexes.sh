#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${GCP_PROJECT:-}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Error: GCP_PROJECT environment variable must be set to the target Google Cloud project." >&2
  exit 1
fi

INDEX_FILE="${SCRIPT_DIR}/../firestore.indexes.json"

if [[ ! -f "${INDEX_FILE}" ]]; then
  echo "Error: Expected index file not found at ${INDEX_FILE}" >&2
  exit 1
fi

echo "Publishing Firestore composite indexes from ${INDEX_FILE} to project ${PROJECT_ID}..."

gcloud firestore indexes composite create \
  --project="${PROJECT_ID}" \
  --file="${INDEX_FILE}" \
  --quiet

echo "Firestore index deployment request submitted. Monitor the Firestore console to confirm completion."
