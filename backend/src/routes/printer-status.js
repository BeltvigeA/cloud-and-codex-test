import express from 'express';
import { query } from '../db/connection.js';
import { authenticate } from '../middleware/auth.js';

const router = express.Router();

// Create/Update printer status (for printer-agent)
router.post('/', authenticate, async (req, res) => {
  try {
    const {
      printer_id, organizationId, status, current_job_id,
      progress_percentage, current_layer, total_layers,
      time_elapsed_seconds, time_remaining_seconds,
      nozzle_temp_current, nozzle_temp_target,
      bed_temp_current, bed_temp_target, chamber_temp_current,
      print_speed_percentage, flow_rate_percentage,
      part_cooling_fan_speed, aux_fan_speed, chamber_fan_speed,
      error_code, error_message, raw_status_data
    } = req.body;

    if (!printer_id || !organizationId || !status) {
      return res.status(400).json({ error: 'printer_id, organizationId, and status required' });
    }

    // Insert new status record
    const result = await query(
      `INSERT INTO printer_status (
        organization_id, printer_id, status, current_job_id,
        progress_percentage, current_layer, total_layers,
        time_elapsed_seconds, time_remaining_seconds,
        nozzle_temp_current, nozzle_temp_target,
        bed_temp_current, bed_temp_target, chamber_temp_current,
        print_speed_percentage, flow_rate_percentage,
        part_cooling_fan_speed, aux_fan_speed, chamber_fan_speed,
        error_code, error_message, raw_status_data
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22)
      RETURNING *`,
      [
        organizationId, printer_id, status, current_job_id,
        progress_percentage, current_layer, total_layers,
        time_elapsed_seconds, time_remaining_seconds,
        nozzle_temp_current, nozzle_temp_target,
        bed_temp_current, bed_temp_target, chamber_temp_current,
        print_speed_percentage, flow_rate_percentage,
        part_cooling_fan_speed, aux_fan_speed, chamber_fan_speed,
        error_code, error_message, raw_status_data ? JSON.stringify(raw_status_data) : null
      ]
    );

    // Update printer's current status
    await query(
      `UPDATE printers SET
        current_status = $1,
        current_job_id = $2,
        last_seen_at = NOW(),
        updated_at = NOW()
       WHERE id = $3`,
      [status, current_job_id, printer_id]
    );

    // Update print job progress if applicable
    if (current_job_id && progress_percentage !== undefined) {
      await query(
        `UPDATE print_jobs SET
          progress_percentage = $1,
          current_layer = $2,
          updated_at = NOW()
         WHERE id = $3`,
        [progress_percentage, current_layer, current_job_id]
      );
    }

    res.status(201).json(result.rows[0]);
  } catch (error) {
    console.error('Error creating printer status:', error);
    res.status(500).json({ error: 'Failed to create printer status' });
  }
});

// Get latest status for printer
router.get('/latest/:printerId', authenticate, async (req, res) => {
  try {
    const { printerId } = req.params;
    const { organizationId } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    const result = await query(
      `SELECT ps.*,
              pj.product_id
       FROM printer_status ps
       LEFT JOIN print_jobs pj ON ps.current_job_id = pj.id
       WHERE ps.printer_id = $1 AND ps.organization_id = $2
       ORDER BY ps.created_at DESC
       LIMIT 1`,
      [printerId, organizationId]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'No status found' });
    }

    res.json(result.rows[0]);
  } catch (error) {
    console.error('Error fetching printer status:', error);
    res.status(500).json({ error: 'Failed to fetch printer status' });
  }
});

// Get status history for printer
router.get('/history/:printerId', authenticate, async (req, res) => {
  try {
    const { printerId } = req.params;
    const { organizationId, limit } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    const result = await query(
      `SELECT * FROM printer_status
       WHERE printer_id = $1 AND organization_id = $2
       ORDER BY created_at DESC
       LIMIT $3`,
      [printerId, organizationId, limit ? parseInt(limit) : 100]
    );

    res.json(result.rows);
  } catch (error) {
    console.error('Error fetching status history:', error);
    res.status(500).json({ error: 'Failed to fetch status history' });
  }
});

export default router;
