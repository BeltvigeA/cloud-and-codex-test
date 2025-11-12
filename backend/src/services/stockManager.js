import { query } from '../db/connection.js';
import pg from 'pg';

const { Pool } = pg;

// Create a dedicated pool for transactions
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: false } : false
});

/**
 * Check if product has sufficient stock
 * @param {string} productId - Product UUID
 * @param {number} requestedQuantity - Requested quantity
 * @returns {Promise} Stock check result
 */
export async function checkStockAvailability(productId, requestedQuantity) {
  const result = await query(
    `SELECT sp.quantity, sp.status, p.name
     FROM stock_products sp
     JOIN products p ON sp.product_id = p.id
     WHERE sp.product_id = $1`,
    [productId]
  );

  if (result.rows.length === 0) {
    return {
      available: false,
      currentStock: 0,
      requested: requestedQuantity,
      canFulfill: false,
      message: 'Product not found in stock'
    };
  }

  const stock = result.rows[0];

  return {
    available: true,
    currentStock: stock.quantity,
    requested: requestedQuantity,
    canFulfill: stock.quantity >= requestedQuantity,
    productName: stock.name,
    status: stock.status,
    message: stock.quantity >= requestedQuantity
      ? 'Sufficient stock available'
      : `Only ${stock.quantity} items available`
  };
}

/**
 * Deduct stock for sale
 * @param {string} organizationId - Organization UUID
 * @param {string} productId - Product UUID
 * @param {number} quantity - Quantity to deduct
 * @param {string} referenceType - Reference type (e.g., 'pos_order')
 * @param {string} referenceId - Reference UUID
 * @param {string} userId - User UUID
 * @returns {Promise} Transaction result
 */
export async function deductStock(organizationId, productId, quantity, referenceType, referenceId, userId) {
  const client = await pool.connect();

  try {
    await client.query('BEGIN');

    // Get current stock
    const stockResult = await client.query(
      `SELECT id, quantity FROM stock_products WHERE product_id = $1 AND organization_id = $2 FOR UPDATE`,
      [productId, organizationId]
    );

    if (stockResult.rows.length === 0) {
      throw new Error('Product not in stock');
    }

    const stock = stockResult.rows[0];
    const quantityBefore = stock.quantity;
    const quantityAfter = quantityBefore - quantity;

    if (quantityAfter < 0) {
      throw new Error(`Insufficient stock. Available: ${quantityBefore}, Requested: ${quantity}`);
    }

    // Update stock
    await client.query(
      `UPDATE stock_products SET
        quantity = $1,
        last_movement_at = NOW(),
        updated_at = NOW()
       WHERE id = $2`,
      [quantityAfter, stock.id]
    );

    // Update stock status based on quantity
    let newStatus = 'in_stock';
    const minStockResult = await client.query(
      `SELECT min_stock FROM stock_products WHERE id = $1`,
      [stock.id]
    );

    if (minStockResult.rows.length > 0) {
      const minStock = minStockResult.rows[0].min_stock || 0;
      if (quantityAfter === 0) {
        newStatus = 'out_of_stock';
      } else if (quantityAfter <= minStock) {
        newStatus = 'low_stock';
      }
    }

    await client.query(
      `UPDATE stock_products SET status = $1 WHERE id = $2`,
      [newStatus, stock.id]
    );

    // Create transaction record
    const transactionResult = await client.query(
      `INSERT INTO stock_transactions (
        organization_id, product_id, stock_product_id,
        transaction_type, quantity_change, quantity_before, quantity_after,
        reference_type, reference_id, performed_by
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
      RETURNING *`,
      [
        organizationId, productId, stock.id,
        'sale', -quantity, quantityBefore, quantityAfter,
        referenceType, referenceId, userId
      ]
    );

    await client.query('COMMIT');

    return {
      success: true,
      stockProductId: stock.id,
      quantityBefore,
      quantityAfter,
      newStatus,
      transaction: transactionResult.rows[0]
    };
  } catch (error) {
    await client.query('ROLLBACK');
    console.error('Stock deduction error:', error);
    throw error;
  } finally {
    client.release();
  }
}

/**
 * Restore stock (for cancellations/refunds)
 * @param {string} organizationId - Organization UUID
 * @param {string} productId - Product UUID
 * @param {number} quantity - Quantity to restore
 * @param {string} referenceType - Reference type
 * @param {string} referenceId - Reference UUID
 * @param {string} userId - User UUID
 * @param {string} reason - Reason for restoration
 * @returns {Promise} Transaction result
 */
export async function restoreStock(organizationId, productId, quantity, referenceType, referenceId, userId, reason) {
  const client = await pool.connect();

  try {
    await client.query('BEGIN');

    // Get current stock
    const stockResult = await client.query(
      `SELECT id, quantity FROM stock_products WHERE product_id = $1 AND organization_id = $2 FOR UPDATE`,
      [productId, organizationId]
    );

    if (stockResult.rows.length === 0) {
      throw new Error('Product not in stock');
    }

    const stock = stockResult.rows[0];
    const quantityBefore = stock.quantity;
    const quantityAfter = quantityBefore + quantity;

    // Update stock
    await client.query(
      `UPDATE stock_products SET
        quantity = $1,
        last_movement_at = NOW(),
        updated_at = NOW(),
        status = 'in_stock'
       WHERE id = $2`,
      [quantityAfter, stock.id]
    );

    // Create transaction record
    const transactionResult = await client.query(
      `INSERT INTO stock_transactions (
        organization_id, product_id, stock_product_id,
        transaction_type, quantity_change, quantity_before, quantity_after,
        reference_type, reference_id, performed_by, reason
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
      RETURNING *`,
      [
        organizationId, productId, stock.id,
        'return', quantity, quantityBefore, quantityAfter,
        referenceType, referenceId, userId, reason
      ]
    );

    await client.query('COMMIT');

    return {
      success: true,
      stockProductId: stock.id,
      quantityBefore,
      quantityAfter,
      transaction: transactionResult.rows[0]
    };
  } catch (error) {
    await client.query('ROLLBACK');
    console.error('Stock restoration error:', error);
    throw error;
  } finally {
    client.release();
  }
}

/**
 * Get low stock products
 * @param {string} organizationId - Organization UUID
 * @returns {Promise} Low stock products
 */
export async function getLowStockProducts(organizationId) {
  const result = await query(
    `SELECT
      p.id,
      p.name,
      p.sku,
      sp.quantity,
      sp.min_stock,
      sp.status
     FROM stock_products sp
     JOIN products p ON sp.product_id = p.id
     WHERE sp.organization_id = $1
       AND sp.status IN ('low_stock', 'out_of_stock')
     ORDER BY sp.quantity ASC`,
    [organizationId]
  );

  return result.rows;
}
