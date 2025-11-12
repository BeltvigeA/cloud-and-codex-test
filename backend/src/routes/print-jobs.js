import express from 'express';
import { query } from '../db/connection.js';
import { authenticate } from '../middleware/auth.js';
import { sendPrintJobToPrinterBackend } from '../services/printerBackend.js';

const router = express.Router();

// Create print job
router.post('/', authenticate, async (req, res) => {
  try {
    const {
      organizationId, product_id, printer_id,
      plates_requested, priority, ams_configuration, notes
    } = req.body;

    if (!organizationId || !product_id) {
      return res.status(400).json({ error: 'organizationId and product_id required' });
    }

    // Verify product exists (if products table exists)
    let totalLayers = null;
    try {
      const productCheck = await query(
        `SELECT id, total_layers FROM products
         WHERE id = $1 AND organization_id = $2`,
        [product_id, organizationId]
      );

      if (productCheck.rows.length === 0) {
        return res.status(404).json({ error: 'Product not found' });
      }

      totalLayers = productCheck.rows[0].total_layers;
    } catch (error) {
      console.warn('Products table may not exist, continuing without product verification');
    }

    // Verify printer exists if specified
    if (printer_id) {
      const printerCheck = await query(
        `SELECT id FROM printers WHERE id = $1 AND organization_id = $2`,
        [printer_id, organizationId]
      );

      if (printerCheck.rows.length === 0) {
        return res.status(404).json({ error: 'Printer not found' });
      }
    }

    // Create job
    const result = await query(
      `INSERT INTO print_jobs (
        organization_id, product_id, printer_id,
        plates_requested, priority, ams_configuration,
        total_layers, notes, created_by
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
      RETURNING *`,
      [
        organizationId, product_id, printer_id,
        plates_requested || 1, priority || 'normal',
        ams_configuration ? JSON.stringify(ams_configuration) : null,
        totalLayers, notes, req.user.id
      ]
    );

    res.status(201).json(result.rows[0]);
  } catch (error) {
    console.error('Error creating print job:', error);
    res.status(500).json({ error: 'Failed to create print job' });
  }
});

// Send job to printer backend
router.post('/:id/send', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId, printerId } = req.body;

    if (!organizationId || !printerId) {
      return res.status(400).json({ error: 'organizationId and printerId required' });
    }

    // Verify job exists and is pending
    const jobCheck = await query(
      `SELECT id, status FROM print_jobs
       WHERE id = $1 AND organization_id = $2`,
      [id, organizationId]
    );

    if (jobCheck.rows.length === 0) {
      return res.status(404).json({ error: 'Print job not found' });
    }

    if (jobCheck.rows[0].status !== 'pending') {
      return res.status(400).json({
        error: `Cannot send job with status: ${jobCheck.rows[0].status}`
      });
    }

    // Update job with printer
    await query(
      `UPDATE print_jobs SET printer_id = $1, updated_at = NOW() WHERE id = $2`,
      [printerId, id]
    );

    // Get auth token from request
    const token = req.headers.authorization?.replace('Bearer ', '');

    // Send to printer backend
    const result = await sendPrintJobToPrinterBackend(id, printerId, token);

    res.json({
      message: 'Job sent to printer backend',
      ...result
    });
  } catch (error) {
    console.error('Error sending job:', error);
    res.status(500).json({ error: error.message });
  }
});

// Get all print jobs
router.get('/', authenticate, async (req, res) => {
  try {
    const { organizationId, status, printerId, limit, offset } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    let queryText = `
      SELECT
        pj.*,
        pr.name as printer_name,
        pr.model as printer_model
      FROM print_jobs pj
      LEFT JOIN printers pr ON pj.printer_id = pr.id
      WHERE pj.organization_id = $1
    `;
    const queryParams = [organizationId];

    if (status) {
      queryText += ` AND pj.status = $${queryParams.length + 1}`;
      queryParams.push(status);
    }

    if (printerId) {
      queryText += ` AND pj.printer_id = $${queryParams.length + 1}`;
      queryParams.push(printerId);
    }

    queryText += ` ORDER BY pj.created_at DESC`;

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
    console.error('Error fetching print jobs:', error);
    res.status(500).json({ error: 'Failed to fetch print jobs' });
  }
});

// Get single print job
router.get('/:id', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    const result = await query(
      `SELECT
        pj.*,
        pr.name as printer_name,
        pr.model as printer_model,
        pr.ip_address as printer_ip
       FROM print_jobs pj
       LEFT JOIN printers pr ON pj.printer_id = pr.id
       WHERE pj.id = $1 AND pj.organization_id = $2`,
      [id, organizationId]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Print job not found' });
    }

    res.json(result.rows[0]);
  } catch (error) {
    console.error('Error fetching print job:', error);
    res.status(500).json({ error: 'Failed to fetch print job' });
  }
});

// Update print job status
router.put('/:id/status', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId, status, progress_percentage, current_layer, failure_reason } = req.body;

    if (!organizationId || !status) {
      return res.status(400).json({ error: 'organizationId and status required' });
    }

    // Build update query
    const updates = ['status = $1', 'updated_at = NOW()'];
    const values = [status];
    let paramIndex = 2;

    if (progress_percentage !== undefined) {
      updates.push(`progress_percentage = $${paramIndex}`);
      values.push(progress_percentage);
      paramIndex++;
    }

    if (current_layer !== undefined) {
      updates.push(`current_layer = $${paramIndex}`);
      values.push(current_layer);
      paramIndex++;
    }

    if (failure_reason) {
      updates.push(`failure_reason = $${paramIndex}`);
      values.push(failure_reason);
      paramIndex++;
    }

    // Set timestamps based on status
    if (status === 'printing') {
      updates.push('started_at = COALESCE(started_at, NOW())');
    }

    if (['completed', 'failed', 'cancelled'].includes(status)) {
      updates.push('completed_at = NOW()');
    }

    values.push(organizationId, id);

    const result = await query(
      `UPDATE print_jobs SET ${updates.join(', ')}
       WHERE organization_id = $${paramIndex} AND id = $${paramIndex + 1}
       RETURNING *`,
      values
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Print job not found' });
    }

    res.json(result.rows[0]);
  } catch (error) {
    console.error('Error updating print job status:', error);
    res.status(500).json({ error: 'Failed to update print job status' });
  }
});

// Cancel print job
router.post('/:id/cancel', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId } = req.body;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    // Verify job exists and can be cancelled
    const jobCheck = await query(
      `SELECT id, status FROM print_jobs
       WHERE id = $1 AND organization_id = $2`,
      [id, organizationId]
    );

    if (jobCheck.rows.length === 0) {
      return res.status(404).json({ error: 'Print job not found' });
    }

    const job = jobCheck.rows[0];

    if (['completed', 'failed', 'cancelled'].includes(job.status)) {
      return res.status(400).json({ error: `Cannot cancel job with status: ${job.status}` });
    }

    // Update job
    await query(
      `UPDATE print_jobs SET
        status = 'cancelled',
        completed_at = NOW(),
        updated_at = NOW()
       WHERE id = $1`,
      [id]
    );

    res.json({ message: 'Print job cancelled successfully' });
  } catch (error) {
    console.error('Error cancelling print job:', error);
    res.status(500).json({ error: 'Failed to cancel print job' });
  }
});

// Delete print job
router.delete('/:id', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    // Verify job exists and can be deleted
    const jobCheck = await query(
      `SELECT id, status FROM print_jobs
       WHERE id = $1 AND organization_id = $2`,
      [id, organizationId]
    );

    if (jobCheck.rows.length === 0) {
      return res.status(404).json({ error: 'Print job not found' });
    }

    const job = jobCheck.rows[0];

    if (job.status === 'printing') {
      return res.status(400).json({ error: 'Cannot delete job while printing' });
    }

    // Delete job
    await query(`DELETE FROM print_jobs WHERE id = $1`, [id]);

    res.json({ message: 'Print job deleted successfully' });
  } catch (error) {
    console.error('Error deleting print job:', error);
    res.status(500).json({ error: 'Failed to delete print job' });
  }
});

export default router;
