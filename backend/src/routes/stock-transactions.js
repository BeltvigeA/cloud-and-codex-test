import express from 'express';
import { query } from '../db/connection.js';
import { authenticate } from '../middleware/auth.js';

const router = express.Router();

// Get stock transactions
router.get('/', authenticate, async (req, res) => {
  try {
    const { organizationId, productId, transactionType, from_date, to_date, limit, offset } = req.query;

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
        st.*,
        p.name as product_name,
        p.sku as product_sku,
        u.email as performed_by_email
      FROM stock_transactions st
      JOIN products p ON st.product_id = p.id
      LEFT JOIN users u ON st.performed_by = u.id
      WHERE st.organization_id = $1
    `;
    const queryParams = [organizationId];

    if (productId) {
      queryText += ` AND st.product_id = $${queryParams.length + 1}`;
      queryParams.push(productId);
    }

    if (transactionType) {
      queryText += ` AND st.transaction_type = $${queryParams.length + 1}`;
      queryParams.push(transactionType);
    }

    if (from_date) {
      queryText += ` AND st.transaction_date >= $${queryParams.length + 1}`;
      queryParams.push(from_date);
    }

    if (to_date) {
      queryText += ` AND st.transaction_date <= $${queryParams.length + 1}`;
      queryParams.push(to_date);
    }

    queryText += ` ORDER BY st.transaction_date DESC`;

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
    console.error('Error fetching stock transactions:', error);
    res.status(500).json({ error: 'Failed to fetch transactions' });
  }
});

export default router;
