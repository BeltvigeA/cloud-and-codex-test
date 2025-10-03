import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from product_store import DuplicateProductIdError, ProductStore


def testProductStoreLoadsEmptyWhenFileMissing(tmp_path: Path):
    storePath = tmp_path / 'products.json'
    productStore = ProductStore(str(storePath))

    products = productStore.loadProducts()

    assert products == {}


def testProductStorePreventsDuplicateIds(tmp_path: Path):
    storePath = tmp_path / 'products.json'
    duplicatePayload = {
        'products': [
            {'productId': 'alpha', 'lastUsed': datetime.now(timezone.utc).isoformat()},
            {'productId': 'alpha', 'lastUsed': datetime.now(timezone.utc).isoformat()},
        ]
    }
    storePath.write_text(json.dumps(duplicatePayload), encoding='utf-8')
    productStore = ProductStore(str(storePath))

    with pytest.raises(DuplicateProductIdError):
        productStore.loadProducts()


def testProductStoreLookupUpdatesLastUsed(tmp_path: Path):
    storePath = tmp_path / 'products.json'
    initialTimestamp = datetime(2023, 1, 1, tzinfo=timezone.utc)
    lookupTimestamp = datetime(2023, 1, 2, tzinfo=timezone.utc)
    payload = {
        'products': [
            {
                'productId': 'alpha',
                'lastUsed': initialTimestamp.isoformat(),
                'name': 'Widget',
            }
        ]
    }
    storePath.write_text(json.dumps(payload), encoding='utf-8')
    productStore = ProductStore(str(storePath))

    lookupResult = productStore.lookupProduct('alpha', now=lookupTimestamp)

    assert lookupResult is not None
    assert lookupResult['productId'] == 'alpha'
    assert lookupResult['lastUsed'] == lookupTimestamp.isoformat()

    updatedPayload = json.loads(storePath.read_text(encoding='utf-8'))
    storedProduct = updatedPayload['products'][0]
    assert storedProduct['lastUsed'] == lookupTimestamp.isoformat()


def testCleanupExpiredProductsRemovesOldEntries(tmp_path: Path):
    storePath = tmp_path / 'products.json'
    nowTimestamp = datetime(2023, 1, 15, tzinfo=timezone.utc)
    payload = {
        'products': [
            {
                'productId': 'recent',
                'lastUsed': (nowTimestamp - timedelta(days=1)).isoformat(),
            },
            {
                'productId': 'stale',
                'lastUsed': (nowTimestamp - timedelta(days=30)).isoformat(),
            },
        ]
    }
    storePath.write_text(json.dumps(payload), encoding='utf-8')
    productStore = ProductStore(str(storePath))

    removedCount = productStore.cleanupExpiredProducts(timedelta(days=14), now=nowTimestamp)

    assert removedCount == 1

    storedPayload = json.loads(storePath.read_text(encoding='utf-8'))
    storedProductIds = {item['productId'] for item in storedPayload['products']}
    assert storedProductIds == {'recent'}
