import express from 'express';
import { query } from '../db/connection.js';
import { authenticate } from '../middleware/auth.js';

const router = express.Router();

// Get payment settings
router.get('/', authenticate, async (req, res) => {
  try {
    const { organizationId } = req.query;

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

    const result = await query(
      `SELECT * FROM payment_settings WHERE organization_id = $1`,
      [organizationId]
    );

    if (result.rows.length === 0) {
      // Create default settings
      const newSettings = await query(
        `INSERT INTO payment_settings (organization_id, created_by)
         VALUES ($1, $2)
         RETURNING *`,
        [organizationId, req.user.id]
      );
      res.json(newSettings.rows[0]);
    } else {
      res.json(result.rows[0]);
    }
  } catch (error) {
    console.error('Error fetching payment settings:', error);
    res.status(500).json({ error: 'Failed to fetch payment settings' });
  }
});

// Update payment settings
router.put('/', authenticate, async (req, res) => {
  try {
    const { organizationId, ...updates } = req.body;

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
      return res.status(403).json({ error: 'Only owners and admins can update payment settings' });
    }

    // Build UPDATE query
    const allowedFields = [
      'stripe_enabled', 'stripe_publishable_key', 'stripe_secret_key', 'stripe_webhook_secret',
      'mobile_pay_enabled', 'mobile_pay_merchant_id', 'mobile_pay_api_key',
      'vipps_enabled', 'vipps_client_id', 'vipps_client_secret', 'vipps_subscription_key',
      'vipps_number', 'vipps_qr_code_url',
      'default_payment_method', 'require_customer_info',
      'country', 'currency', 'default_tax_percentage',
      'company_logo_url', 'receipt_footer_text'
    ];

    const updateFields = [];
    const updateValues = [];
    let paramIndex = 1;

    for (const [key, value] of Object.entries(updates)) {
      if (allowedFields.includes(key)) {
        updateFields.push(`${key} = $${paramIndex}`);
        updateValues.push(value);
        paramIndex++;
      }
    }

    if (updateFields.length === 0) {
      return res.status(400).json({ error: 'No valid fields to update' });
    }

    updateFields.push('updated_at = NOW()');
    updateValues.push(organizationId);

    const queryText = `
      UPDATE payment_settings
      SET ${updateFields.join(', ')}
      WHERE organization_id = $${paramIndex}
      RETURNING *
    `;

    const result = await query(queryText, updateValues);

    if (result.rows.length === 0) {
      // Create if doesn't exist
      const newSettings = await query(
        `INSERT INTO payment_settings (organization_id, created_by)
         VALUES ($1, $2)
         RETURNING *`,
        [organizationId, req.user.id]
      );
      res.json(newSettings.rows[0]);
    } else {
      res.json(result.rows[0]);
    }
  } catch (error) {
    console.error('Error updating payment settings:', error);
    res.status(500).json({ error: 'Failed to update payment settings' });
  }
});

export default router;
