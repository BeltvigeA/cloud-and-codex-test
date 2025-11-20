import express from 'express';
import { query } from '../db.js';
import { authenticatePrinterClient } from '../middleware/printerAuth.js';

const router = express.Router();

/**
 * Update printer status
 * POST /api/printer-status
 */
router.post('/', authenticatePrinterClient, async (req, res) => {
  try {
    const {
      printerSerial,
      status,
      progress,
      bedTemp,
      nozzleTemp,
      lastCommandId,
      errors,
      metadata
    } = req.body;

    if (!printerSerial) {
      return res.status(400).json({ error: 'printerSerial is required' });
    }

    // Get organizationId from authenticatePrinterClient middleware
    const organizationId = req.organizationId;

    // Check if printer exists, if not create it
    const printerCheck = await query(
      `SELECT id FROM printers WHERE serial_number = $1 AND organization_id = $2`,
      [printerSerial, organizationId]
    );

    let printerId;
    if (printerCheck.rows.length === 0) {
      // Create new printer
      const newPrinter = await query(
        `INSERT INTO printers (serial_number, organization_id, status, created_at, updated_at)
         VALUES ($1, $2, $3, NOW(), NOW())
         RETURNING id`,
        [printerSerial, organizationId, status || 'idle']
      );
      printerId = newPrinter.rows[0].id;
      console.log(`📟 New printer registered: ${printerSerial} for organization ${organizationId}`);
    } else {
      printerId = printerCheck.rows[0].id;
    }

    // Update printer status
    const updateResult = await query(
      `UPDATE printers
       SET
         status = COALESCE($1, status),
         progress = COALESCE($2, progress),
         bed_temp = COALESCE($3, bed_temp),
         nozzle_temp = COALESCE($4, nozzle_temp),
         last_command_id = COALESCE($5, last_command_id),
         errors = COALESCE($6, errors),
         metadata = COALESCE($7, metadata),
         last_seen_at = NOW(),
         updated_at = NOW()
       WHERE id = $8
       RETURNING id, serial_number, status, progress`,
      [status, progress, bedTemp, nozzleTemp, lastCommandId, errors, metadata ? JSON.stringify(metadata) : null, printerId]
    );

    if (updateResult.rows.length === 0) {
      return res.status(404).json({ error: 'Failed to update printer status' });
    }

    const updatedPrinter = updateResult.rows[0];

    console.log(`📊 Printer status updated: ${printerSerial} - ${status || 'unknown'} (${progress || 0}%)`);

    res.json({
      success: true,
      data: updatedPrinter
    });
  } catch (error) {
    console.error('Update printer status error:', error);
    res.status(500).json({ error: 'Server error' });
  }
});

/**
 * Get printer status
 * GET /api/printer-status/:serial
 */
router.get('/:serial', authenticatePrinterClient, async (req, res) => {
  try {
    const { serial } = req.params;
    const organizationId = req.organizationId;

    const result = await query(
      `SELECT
        id,
        serial_number,
        status,
        progress,
        bed_temp,
        nozzle_temp,
        last_command_id,
        errors,
        metadata,
        last_seen_at,
        updated_at
      FROM printers
      WHERE serial_number = $1 AND organization_id = $2`,
      [serial, organizationId]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Printer not found' });
    }

    res.json({
      data: result.rows[0]
    });
  } catch (error) {
    console.error('Get printer status error:', error);
    res.status(500).json({ error: 'Server error' });
  }
});

export default router;
