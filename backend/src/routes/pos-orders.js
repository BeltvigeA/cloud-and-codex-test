import express from 'express';
import { query } from '../db/connection.js';
import { authenticate } from '../middleware/auth.js';
import { createPaymentIntent, createRefund } from '../services/stripe.js';
import { deductStock, restoreStock, checkStockAvailability } from '../services/stockManager.js';
import { generateReceipt } from '../services/receiptGenerator.js';
import pg from 'pg';

const { Pool } = pg;
const router = express.Router();

// Create a dedicated pool for transactions
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: false } : false
});

// Create new POS order
router.post('/', authenticate, async (req, res) => {
  try {
    const {
      organizationId, customer_name, customer_email, customer_phone,
      note, lines
    } = req.body;

    if (!organizationId || !lines || lines.length === 0) {
      return res.status(400).json({ error: 'organizationId and lines required' });
    }

    // Verify access
    const accessCheck = await query(
      `SELECT role FROM organization_members
       WHERE organization_id = $1 AND user_id = $2 AND is_active = true`,
      [organizationId, req.user.id]
    );

    if (accessCheck.rows.length === 0) {
      return res.status(403).json({ error: 'Access denied' });
    }

    const client = await pool.connect();

    try {
      await client.query('BEGIN');

      // Calculate totals
      let subtotal = 0;
      const enrichedLines = [];

      for (const line of lines) {
        // Get product details
        const productResult = await client.query(
          `SELECT p.*, sp.quantity as stock_quantity
           FROM products p
           LEFT JOIN stock_products sp ON p.id = sp.product_id
           WHERE p.id = $1 AND p.organization_id = $2`,
          [line.product_id, organizationId]
        );

        if (productResult.rows.length === 0) {
          throw new Error(`Product ${line.product_id} not found`);
        }

        const product = productResult.rows[0];

        // Check stock availability
        if (product.stock_tracked) {
          const stockCheck = await checkStockAvailability(product.id, line.quantity);
          if (!stockCheck.canFulfill && !line.allow_backorder) {
            throw new Error(`Insufficient stock for ${product.name}. Available: ${stockCheck.currentStock}`);
          }
        }

        const unitPrice = line.unit_price || product.price || 0;
        const lineTotal = unitPrice * line.quantity - (line.discount_amount || 0);

        subtotal += lineTotal;

        enrichedLines.push({
          product_id: product.id,
          product_name: product.name,
          product_sku: product.sku,
          quantity: line.quantity,
          unit_price: unitPrice,
          discount_amount: line.discount_amount || 0,
          line_total: lineTotal,
          allow_backorder: line.allow_backorder || false,
          stock_tracked: product.stock_tracked
        });
      }

      // Get payment settings for tax
      const settingsResult = await client.query(
        `SELECT default_tax_percentage, currency FROM payment_settings WHERE organization_id = $1`,
        [organizationId]
      );

      const taxPercentage = settingsResult.rows[0]?.default_tax_percentage || 0;
      const currency = settingsResult.rows[0]?.currency || 'NOK';
      const taxAmount = (subtotal * taxPercentage) / 100;
      const totalAmount = subtotal + taxAmount;

      // Create order
      const orderResult = await client.query(
        `INSERT INTO pos_orders (
          organization_id, customer_name, customer_email, customer_phone,
          subtotal, tax_percentage, tax_amount, total_amount,
          note, created_by
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING *`,
        [
          organizationId, customer_name, customer_email, customer_phone,
          subtotal, taxPercentage, taxAmount, totalAmount,
          note, req.user.id
        ]
      );

      const order = orderResult.rows[0];

      // Create order lines
      for (const line of enrichedLines) {
        await client.query(
          `INSERT INTO pos_order_lines (
            organization_id, order_id, product_id, product_name, product_sku,
            quantity, unit_price, discount_amount, line_total,
            allow_backorder, created_by
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)`,
          [
            organizationId, order.id, line.product_id, line.product_name, line.product_sku,
            line.quantity, line.unit_price, line.discount_amount, line.line_total,
            line.allow_backorder, req.user.id
          ]
        );
      }

      await client.query('COMMIT');

      // Return order with lines
      const completeOrder = await query(
        `SELECT
          po.*,
          json_agg(
            json_build_object(
              'id', pol.id,
              'product_id', pol.product_id,
              'product_name', pol.product_name,
              'quantity', pol.quantity,
              'unit_price', pol.unit_price,
              'line_total', pol.line_total
            ) ORDER BY pol.created_at
          ) as lines
         FROM pos_orders po
         LEFT JOIN pos_order_lines pol ON po.id = pol.order_id
         WHERE po.id = $1
         GROUP BY po.id`,
        [order.id]
      );

      res.status(201).json(completeOrder.rows[0]);
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Error creating POS order:', error);
    res.status(500).json({ error: error.message });
  }
});

// Process payment
router.post('/:id/pay', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId, payment_method, amount_paid } = req.body;

    if (!organizationId || !payment_method) {
      return res.status(400).json({ error: 'organizationId and payment_method required' });
    }

    // Verify access
    const accessCheck = await query(
      `SELECT role FROM organization_members
       WHERE organization_id = $1 AND user_id = $2 AND is_active = true`,
      [organizationId, req.user.id]
    );

    if (accessCheck.rows.length === 0) {
      return res.status(403).json({ error: 'Access denied' });
    }

    // Get order with lines
    const orderResult = await query(
      `SELECT * FROM pos_orders WHERE id = $1 AND organization_id = $2`,
      [id, organizationId]
    );

    if (orderResult.rows.length === 0) {
      return res.status(404).json({ error: 'Order not found' });
    }

    const order = orderResult.rows[0];

    if (order.status === 'Paid') {
      return res.status(400).json({ error: 'Order already paid' });
    }

    const client = await pool.connect();

    try {
      await client.query('BEGIN');

      let paymentIntentId = null;
      let paymentStatus = 'completed';
      let changeGiven = 0;

      // Process payment based on method
      if (payment_method === 'Stripe') {
        // Get payment settings
        const settingsResult = await client.query(
          `SELECT currency FROM payment_settings WHERE organization_id = $1`,
          [organizationId]
        );

        const currency = settingsResult.rows[0]?.currency || 'NOK';

        // Create Stripe payment intent
        const paymentIntent = await createPaymentIntent(
          organizationId,
          order.total_amount * 100, // Convert to cents/øre
          currency,
          { orderId: order.id, receiptNumber: order.receipt_number }
        );

        paymentIntentId = paymentIntent.paymentIntentId;
        paymentStatus = 'processing';

      } else if (payment_method === 'Cash') {
        if (!amount_paid) {
          throw new Error('amount_paid required for cash payment');
        }

        if (amount_paid < order.total_amount) {
          throw new Error('Insufficient payment amount');
        }

        changeGiven = amount_paid - order.total_amount;
        paymentStatus = 'completed';

      } else if (payment_method === 'Vipps' || payment_method === 'MobilePay') {
        // Placeholder for Vipps/MobilePay integration
        paymentStatus = 'processing';
      }

      // Update order
      await client.query(
        `UPDATE pos_orders SET
          payment_method = $1,
          payment_status = $2,
          payment_intent_id = $3,
          amount_paid = $4,
          change_given = $5,
          status = CASE WHEN $2 = 'completed' THEN 'Paid' ELSE status END,
          paid_at = CASE WHEN $2 = 'completed' THEN NOW() ELSE NULL END,
          sale_timestamp = CASE WHEN sale_timestamp IS NULL THEN NOW() ELSE sale_timestamp END,
          updated_at = NOW()
         WHERE id = $6`,
        [payment_method, paymentStatus, paymentIntentId, amount_paid || order.total_amount, changeGiven, id]
      );

      // Deduct stock if payment completed
      if (paymentStatus === 'completed') {
        // Get order lines
        const linesResult = await client.query(
          `SELECT * FROM pos_order_lines WHERE order_id = $1`,
          [id]
        );

        for (const line of linesResult.rows) {
          // Check if product is stock tracked
          const productResult = await client.query(
            `SELECT stock_tracked FROM products WHERE id = $1`,
            [line.product_id]
          );

          if (productResult.rows[0]?.stock_tracked) {
            await deductStock(
              organizationId,
              line.product_id,
              line.quantity,
              'pos_order',
              id,
              req.user.id
            );

            // Mark stock as deducted
            await client.query(
              `UPDATE pos_order_lines SET stock_deducted = true WHERE id = $1`,
              [line.id]
            );
          }
        }

        // Create finance income record
        await client.query(
          `INSERT INTO finance_incomes (
            organization_id, date, amount, vat_pct, category,
            customer, pos_order_id, notes, created_by
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)`,
          [
            organizationId,
            new Date(),
            order.total_amount,
            order.tax_percentage,
            'Product Sales',
            order.customer_name || 'Walk-in Customer',
            id,
            `POS Sale - Receipt ${order.receipt_number}`,
            req.user.id
          ]
        );

        // Generate receipt (don't await, let it run async)
        generateReceipt(id, organizationId).catch(err => {
          console.error('Failed to generate receipt:', err);
        });
      }

      await client.query('COMMIT');

      // Get updated order
      const updatedOrder = await query(
        `SELECT * FROM pos_orders WHERE id = $1`,
        [id]
      );

      res.json({
        order: updatedOrder.rows[0],
        paymentIntentId,
        changeGiven: changeGiven > 0 ? changeGiven : null
      });
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Error processing payment:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get all POS orders
router.get('/', authenticate, async (req, res) => {
  try {
    const { organizationId, status, payment_status, from_date, to_date, limit, offset } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    // Verify access
    const accessCheck = await query(
      `SELECT role FROM organization_members
       WHERE organization_id = $1 AND user_id = $2 AND is_active = true`,
      [organizationId, req.user.id]
    );

    if (accessCheck.rows.length === 0) {
      return res.status(403).json({ error: 'Access denied' });
    }

    let queryText = `
      SELECT
        po.*,
        COUNT(pol.id) as line_count,
        u.email as created_by_email
      FROM pos_orders po
      LEFT JOIN pos_order_lines pol ON po.id = pol.order_id
      LEFT JOIN users u ON po.created_by = u.id
      WHERE po.organization_id = $1
    `;
    const queryParams = [organizationId];

    if (status) {
      queryText += ` AND po.status = $${queryParams.length + 1}`;
      queryParams.push(status);
    }

    if (payment_status) {
      queryText += ` AND po.payment_status = $${queryParams.length + 1}`;
      queryParams.push(payment_status);
    }

    if (from_date) {
      queryText += ` AND po.created_at >= $${queryParams.length + 1}`;
      queryParams.push(from_date);
    }

    if (to_date) {
      queryText += ` AND po.created_at <= $${queryParams.length + 1}`;
      queryParams.push(to_date);
    }

    queryText += ` GROUP BY po.id, u.email ORDER BY po.created_at DESC`;

    if (limit) {
      queryText += ` LIMIT $${queryParams.length + 1}`;
      queryParams.push(parseInt(limit));
    }

    if (offset) {
      queryText += ` OFFSET $${queryParams.length + 1}`;
      queryParams.push(parseInt(offset));
    }

    const result = await query(queryText, queryParams);
    res.json(result.rows);
  } catch (error) {
    console.error('Error fetching POS orders:', error);
    res.status(500).json({ error: 'Failed to fetch orders' });
  }
});

// Get single POS order with lines
router.get('/:id', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    const result = await query(
      `SELECT
        po.*,
        json_agg(
          json_build_object(
            'id', pol.id,
            'product_id', pol.product_id,
            'product_name', pol.product_name,
            'product_sku', pol.product_sku,
            'quantity', pol.quantity,
            'unit_price', pol.unit_price,
            'discount_amount', pol.discount_amount,
            'line_total', pol.line_total,
            'stock_deducted', pol.stock_deducted
          ) ORDER BY pol.created_at
        ) as lines
       FROM pos_orders po
       LEFT JOIN pos_order_lines pol ON po.id = pol.order_id
       JOIN organization_members om ON po.organization_id = om.organization_id
       WHERE po.id = $1 AND po.organization_id = $2 AND om.user_id = $3 AND om.is_active = true
       GROUP BY po.id`,
      [id, organizationId, req.user.id]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Order not found' });
    }

    res.json(result.rows[0]);
  } catch (error) {
    console.error('Error fetching POS order:', error);
    res.status(500).json({ error: 'Failed to fetch order' });
  }
});

// Cancel order
router.post('/:id/cancel', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId, reason } = req.body;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    // Verify access
    const accessCheck = await query(
      `SELECT role FROM organization_members
       WHERE organization_id = $1 AND user_id = $2 AND is_active = true`,
      [organizationId, req.user.id]
    );

    if (accessCheck.rows.length === 0) {
      return res.status(403).json({ error: 'Access denied' });
    }

    // Get order
    const orderResult = await query(
      `SELECT * FROM pos_orders WHERE id = $1 AND organization_id = $2`,
      [id, organizationId]
    );

    if (orderResult.rows.length === 0) {
      return res.status(404).json({ error: 'Order not found' });
    }

    const order = orderResult.rows[0];

    if (order.status === 'Paid') {
      return res.status(400).json({ error: 'Cannot cancel paid order. Use refund instead.' });
    }

    const client = await pool.connect();

    try {
      await client.query('BEGIN');

      // Restore stock if it was deducted
      const linesResult = await client.query(
        `SELECT * FROM pos_order_lines WHERE order_id = $1 AND stock_deducted = true`,
        [id]
      );

      for (const line of linesResult.rows) {
        await restoreStock(
          organizationId,
          line.product_id,
          line.quantity,
          'pos_order',
          id,
          req.user.id,
          reason || 'Order cancelled'
        );
      }

      // Cancel order
      await client.query(
        `UPDATE pos_orders SET
          status = 'Cancelled',
          internal_notes = $1,
          updated_at = NOW()
         WHERE id = $2`,
        [reason || 'Order cancelled', id]
      );

      await client.query('COMMIT');

      res.json({ message: 'Order cancelled successfully' });
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Error cancelling order:', error);
    res.status(500).json({ error: error.message });
  }
});

// Refund order
router.post('/:id/refund', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId, reason, amount } = req.body;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    // Verify access (owner/admin only)
    const accessCheck = await query(
      `SELECT role FROM organization_members
       WHERE organization_id = $1 AND user_id = $2 AND is_active = true`,
      [organizationId, req.user.id]
    );

    if (accessCheck.rows.length === 0) {
      return res.status(403).json({ error: 'Access denied' });
    }

    const role = accessCheck.rows[0].role;
    if (!['owner', 'admin'].includes(role)) {
      return res.status(403).json({ error: 'Only owners and admins can process refunds' });
    }

    // Get order
    const orderResult = await query(
      `SELECT * FROM pos_orders WHERE id = $1 AND organization_id = $2`,
      [id, organizationId]
    );

    if (orderResult.rows.length === 0) {
      return res.status(404).json({ error: 'Order not found' });
    }

    const order = orderResult.rows[0];

    if (order.status !== 'Paid') {
      return res.status(400).json({ error: 'Can only refund paid orders' });
    }

    const client = await pool.connect();

    try {
      await client.query('BEGIN');

      // Process Stripe refund if applicable
      if (order.payment_method === 'Stripe' && order.payment_intent_id) {
        await createRefund(organizationId, order.payment_intent_id, amount ? amount * 100 : null);
      }

      // Restore stock
      const linesResult = await client.query(
        `SELECT * FROM pos_order_lines WHERE order_id = $1`,
        [id]
      );

      for (const line of linesResult.rows) {
        // Check if product is stock tracked
        const productResult = await client.query(
          `SELECT stock_tracked FROM products WHERE id = $1`,
          [line.product_id]
        );

        if (productResult.rows[0]?.stock_tracked) {
          await restoreStock(
            organizationId,
            line.product_id,
            line.quantity,
            'pos_order',
            id,
            req.user.id,
            reason || 'Order refunded'
          );
        }
      }

      // Update order
      await client.query(
        `UPDATE pos_orders SET
          status = 'Refunded',
          payment_status = 'refunded',
          refunded_at = NOW(),
          internal_notes = $1,
          updated_at = NOW()
         WHERE id = $2`,
        [reason || 'Order refunded', id]
      );

      // Create negative finance income record
      await client.query(
        `INSERT INTO finance_incomes (
          organization_id, date, amount, vat_pct, category,
          customer, pos_order_id, notes, created_by
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)`,
        [
          organizationId,
          new Date(),
          -(amount || order.total_amount),
          order.tax_percentage,
          'Product Sales Refund',
          order.customer_name || 'Walk-in Customer',
          id,
          `POS Refund - ${reason || 'Customer return'}`,
          req.user.id
        ]
      );

      await client.query('COMMIT');

      res.json({ message: 'Order refunded successfully' });
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Error refunding order:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get receipt PDF
router.get('/:id/receipt', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    const result = await query(
      `SELECT receipt_pdf_path FROM pos_orders
       WHERE id = $1 AND organization_id = $2`,
      [id, organizationId]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Order not found' });
    }

    if (!result.rows[0].receipt_pdf_path) {
      // Generate receipt if it doesn't exist
      const receiptResult = await generateReceipt(id, organizationId);
      res.json({ receiptPath: receiptResult.receiptPath });
    } else {
      res.json({ receiptPath: result.rows[0].receipt_pdf_path });
    }
  } catch (error) {
    console.error('Error getting receipt:', error);
    res.status(500).json({ error: 'Failed to get receipt' });
  }
});

export default router;
