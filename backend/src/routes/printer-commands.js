import express from 'express';
import { query } from '../db/connection.js';
import { authenticate } from '../middleware/auth.js';

const router = express.Router();

// Create printer command
router.post('/', authenticate, async (req, res) => {
  try {
    const {
      organizationId, printer_id, command_type, metadata
    } = req.body;

    if (!organizationId || !printer_id || !command_type) {
      return res.status(400).json({ error: 'organizationId, printer_id, and command_type required' });
    }

    // Get printer and recipient_id
    let recipientId = 'default-recipient';
    let printerIpAddress = null;

    try {
      const printerResult = await query(
        `SELECT p.id, p.ip_address
         FROM printers p
         WHERE p.id = $1 AND p.organization_id = $2
         LIMIT 1`,
        [printer_id, organizationId]
      );

      if (printerResult.rows.length === 0) {
        return res.status(404).json({ error: 'Printer not found' });
      }

      printerIpAddress = printerResult.rows[0].ip_address;

      // Try to get recipient ID from user_settings
      const settingsResult = await query(
        `SELECT default_recipient_id FROM user_settings
         WHERE organization_id = $1 LIMIT 1`,
        [organizationId]
      );

      if (settingsResult.rows.length > 0 && settingsResult.rows[0].default_recipient_id) {
        recipientId = settingsResult.rows[0].default_recipient_id;
      }
    } catch (error) {
      console.warn('Error fetching printer or settings:', error);
      return res.status(404).json({ error: 'Printer not found' });
    }

    // Create command
    const result = await query(
      `INSERT INTO printer_commands (
        organization_id, printer_id, recipient_id, printer_ip_address,
        command_type, metadata, created_by
      ) VALUES ($1, $2, $3, $4, $5, $6, $7)
      RETURNING *`,
      [
        organizationId, printer_id, recipientId, printerIpAddress,
        command_type, metadata ? JSON.stringify(metadata) : null, req.user.id
      ]
    );

    res.status(201).json(result.rows[0]);
  } catch (error) {
    console.error('Error creating printer command:', error);
    res.status(500).json({ error: 'Failed to create printer command' });
  }
});

// Get pending commands (for printer-agent polling)
router.get('/pending', authenticate, async (req, res) => {
  try {
    const { recipientId, printerId } = req.query;

    if (!recipientId) {
      return res.status(400).json({ error: 'recipientId required' });
    }

    let queryText = `
      SELECT pc.*,
             p.name as printer_name,
             p.ip_address
      FROM printer_commands pc
      JOIN printers p ON pc.printer_id = p.id
      WHERE pc.recipient_id = $1
        AND pc.status = 'pending'
      ORDER BY pc.created_at ASC
    `;
    const queryParams = [recipientId];

    if (printerId) {
      queryText = queryText.replace('ORDER BY', 'AND pc.printer_id = $2 ORDER BY');
      queryParams.push(printerId);
    }

    const result = await query(queryText, queryParams);
    res.json(result.rows);
  } catch (error) {
    console.error('Error fetching pending commands:', error);
    res.status(500).json({ error: 'Failed to fetch pending commands' });
  }
});

// Get all commands for a printer
router.get('/', authenticate, async (req, res) => {
  try {
    const { organizationId, printerId, status, limit } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    let queryText = `
      SELECT pc.*,
             p.name as printer_name
      FROM printer_commands pc
      JOIN printers p ON pc.printer_id = p.id
      WHERE pc.organization_id = $1
    `;
    const queryParams = [organizationId];

    if (printerId) {
      queryText += ` AND pc.printer_id = $${queryParams.length + 1}`;
      queryParams.push(printerId);
    }

    if (status) {
      queryText += ` AND pc.status = $${queryParams.length + 1}`;
      queryParams.push(status);
    }

    queryText += ` ORDER BY pc.created_at DESC`;

    if (limit) {
      queryText += ` LIMIT $${queryParams.length + 1}`;
      queryParams.push(parseInt(limit));
    }

    const result = await query(queryText, queryParams);
    res.json(result.rows);
  } catch (error) {
    console.error('Error fetching printer commands:', error);
    res.status(500).json({ error: 'Failed to fetch printer commands' });
  }
});

// Update command status (for printer-agent)
router.put('/:id/status', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { status, result: cmdResult, error_message } = req.body;

    if (!status) {
      return res.status(400).json({ error: 'status required' });
    }

    const updates = ['status = $1', 'updated_at = NOW()'];
    const values = [status];
    let paramIndex = 2;

    if (cmdResult) {
      updates.push(`result = $${paramIndex}`);
      values.push(cmdResult);
      paramIndex++;
    }

    if (error_message) {
      updates.push(`error_message = $${paramIndex}`);
      values.push(error_message);
      paramIndex++;
    }

    if (status === 'sent') {
      updates.push('sent_at = NOW()');
    }

    if (['completed', 'failed', 'timeout'].includes(status)) {
      updates.push('completed_at = NOW()');
    }

    values.push(id);

    const updateResult = await query(
      `UPDATE printer_commands SET ${updates.join(', ')}
       WHERE id = $${paramIndex}
       RETURNING *`,
      values
    );

    if (updateResult.rows.length === 0) {
      return res.status(404).json({ error: 'Command not found' });
    }

    res.json(updateResult.rows[0]);
  } catch (error) {
    console.error('Error updating command status:', error);
    res.status(500).json({ error: 'Failed to update command status' });
  }
});

// Delete command
router.delete('/:id', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    const result = await query(
      `DELETE FROM printer_commands
       WHERE id = $1 AND organization_id = $2
       RETURNING *`,
      [id, organizationId]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Command not found' });
    }

    res.json({ message: 'Command deleted successfully' });
  } catch (error) {
    console.error('Error deleting command:', error);
    res.status(500).json({ error: 'Failed to delete command' });
  }
});

export default router;
