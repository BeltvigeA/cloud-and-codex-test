"""Persistence helper for managing product metadata."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional


class ProductStoreError(RuntimeError):
    """Base error for product store failures."""


class DuplicateProductIdError(ProductStoreError):
    def __init__(self, productId: str):
        self.productId = productId
        super().__init__(f'Duplicate product_id detected: {productId}')


class MalformedProductError(ProductStoreError):
    pass


@dataclass
class ProductRecord:
    productId: str
    lastUsed: datetime
    attributes: Dict[str, object] = field(default_factory=dict)

    def toJson(self) -> Dict[str, object]:
        payload = dict(self.attributes)
        payload['productId'] = self.productId
        payload['lastUsed'] = self.lastUsed.astimezone(timezone.utc).isoformat()
        return payload

    @classmethod
    def fromJson(cls, payload: Dict[str, object]) -> 'ProductRecord':
        if not isinstance(payload, dict):
            raise MalformedProductError('Product payload must be a JSON object')

        productIdValue = _extractProductId(payload)
        lastUsedValue = _extractLastUsed(payload)
        attributes = {
            key: value
            for key, value in payload.items()
            if key not in {'productId', 'product_id', 'lastUsed', 'last_used'}
        }
        return cls(productId=productIdValue, lastUsed=lastUsedValue, attributes=attributes)


class ProductStore:
    def __init__(self, filePath: str):
        self.filePath = filePath

    def loadProducts(self) -> Dict[str, ProductRecord]:
        if not os.path.exists(self.filePath):
            return {}

        with open(self.filePath, 'r', encoding='utf-8') as fileObj:
            rawContent = fileObj.read().strip()
            if not rawContent:
                return {}
            data = json.loads(rawContent)

        productPayloads = _normalizePayloadCollection(data)
        products: Dict[str, ProductRecord] = {}
        for payload in productPayloads:
            product = ProductRecord.fromJson(payload)
            if product.productId in products:
                raise DuplicateProductIdError(product.productId)
            products[product.productId] = product

        return products

    def saveProducts(self, products: Dict[str, ProductRecord]) -> None:
        directoryPath = os.path.dirname(self.filePath)
        if directoryPath:
            os.makedirs(directoryPath, exist_ok=True)

        serializedProducts = [record.toJson() for record in products.values()]
        serializedProducts.sort(key=lambda item: str(item['productId']))

        with open(self.filePath, 'w', encoding='utf-8') as fileObj:
            json.dump({'products': serializedProducts}, fileObj, indent=2, sort_keys=False)
            fileObj.write('\n')

    def lookupProduct(self, productId: str, now: Optional[datetime] = None) -> Optional[Dict[str, object]]:
        products = self.loadProducts()
        product = products.get(productId)
        if product is None:
            return None

        timestamp = _ensureAwareTimestamp(now)
        product.lastUsed = timestamp
        self.saveProducts(products)
        return product.toJson()

    def cleanupExpiredProducts(
        self, retentionPeriod: timedelta, now: Optional[datetime] = None
    ) -> int:
        products = self.loadProducts()
        if not products:
            return 0

        threshold = _ensureAwareTimestamp(now) - retentionPeriod
        productIdsToRemove = [
            productId for productId, record in products.items() if record.lastUsed < threshold
        ]
        if not productIdsToRemove:
            return 0

        for productId in productIdsToRemove:
            del products[productId]

        self.saveProducts(products)
        logging.info('Removed %s expired products', len(productIdsToRemove))
        return len(productIdsToRemove)


def _normalizePayloadCollection(data: object) -> Iterable[Dict[str, object]]:
    if data is None:
        return []
    if isinstance(data, dict):
        if 'products' in data and isinstance(data['products'], list):
            return [item for item in data['products'] if isinstance(item, dict)]
        if all(isinstance(value, dict) for value in data.values()):
            return list(data.values())
        raise MalformedProductError('Unsupported JSON format for products collection')
    if isinstance(data, list):
        if all(isinstance(item, dict) for item in data):
            return data
        raise MalformedProductError('Product list must contain JSON objects')
    raise MalformedProductError('Unsupported JSON structure for products')


def _extractProductId(payload: Dict[str, object]) -> str:
    productIdValue = payload.get('productId') or payload.get('product_id')
    if not isinstance(productIdValue, str) or not productIdValue.strip():
        raise MalformedProductError('Product must include a non-empty productId')
    return productIdValue.strip()


def _extractLastUsed(payload: Dict[str, object]) -> datetime:
    rawValue = payload.get('lastUsed') or payload.get('last_used')
    if rawValue is None:
        return _ensureAwareTimestamp(None)
    if not isinstance(rawValue, str):
        raise MalformedProductError('lastUsed must be a string timestamp')
    try:
        parsed = datetime.fromisoformat(rawValue)
    except ValueError as error:
        raise MalformedProductError('Invalid ISO8601 timestamp for lastUsed') from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _ensureAwareTimestamp(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    'ProductRecord',
    'ProductStore',
    'ProductStoreError',
    'DuplicateProductIdError',
    'MalformedProductError',
]
