import express from 'express';
import { query } from '../db/connection.js';
import { authenticate } from '../middleware/auth.js';

const router = express.Router();

// Create printer
router.post('/', authenticate, async (req, res) => {
  try {
    const {
      organizationId, name, brand, model, serial_number,
      connection_type, ip_address, access_code,
      mqtt_broker, mqtt_username, mqtt_password,
      num_ams_units, max_build_volume_x, max_build_volume_y, max_build_volume_z,
      max_nozzle_temp, max_bed_temp, supported_materials,
      default_nozzle_size, location, notes
    } = req.body;

    if (!organizationId || !name) {
      return res.status(400).json({ error: 'organizationId and name required' });
    }

    // Verify access (simplified - assumes user has access if they're authenticated)
    // In production, check organization_members table
    const result = await query(
      `INSERT INTO printers (
        organization_id, name, brand, model, serial_number,
        connection_type, ip_address, access_code,
        mqtt_broker, mqtt_username, mqtt_password,
        num_ams_units, max_build_volume_x, max_build_volume_y, max_build_volume_z,
        max_nozzle_temp, max_bed_temp, supported_materials,
        default_nozzle_size, location, notes, created_by
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22)
      RETURNING *`,
      [
        organizationId, name, brand, model, serial_number,
        connection_type || 'network', ip_address, access_code,
        mqtt_broker, mqtt_username, mqtt_password,
        num_ams_units || 0, max_build_volume_x, max_build_volume_y, max_build_volume_z,
        max_nozzle_temp, max_bed_temp, supported_materials,
        default_nozzle_size || 0.4, location, notes, req.user.id
      ]
    );

    res.status(201).json(result.rows[0]);
  } catch (error) {
    console.error('Error creating printer:', error);
    res.status(500).json({ error: 'Failed to create printer' });
  }
});

// Get all printers
router.get('/', authenticate, async (req, res) => {
  try {
    const { organizationId, status } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    let queryText = `
      SELECT p.*,
             ps.status as live_status,
             ps.progress_percentage,
             ps.nozzle_temp_current,
             ps.bed_temp_current
      FROM printers p
      LEFT JOIN LATERAL (
        SELECT * FROM printer_status
        WHERE printer_id = p.id
        ORDER BY created_at DESC
        LIMIT 1
      ) ps ON true
      WHERE p.organization_id = $1
    `;
    const queryParams = [organizationId];

    if (status) {
      queryText += ` AND p.current_status = $${queryParams.length + 1}`;
      queryParams.push(status);
    }

    queryText += ` ORDER BY p.name`;

    const result = await query(queryText, queryParams);
    res.json(result.rows);
  } catch (error) {
    console.error('Error fetching printers:', error);
    res.status(500).json({ error: 'Failed to fetch printers' });
  }
});

// Get single printer
router.get('/:id', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    const result = await query(
      `SELECT p.*,
              ps.status as live_status,
              ps.progress_percentage,
              ps.current_layer,
              ps.total_layers,
              ps.nozzle_temp_current,
              ps.nozzle_temp_target,
              ps.bed_temp_current,
              ps.bed_temp_target,
              ps.time_remaining_seconds,
              ps.last_update_timestamp
       FROM printers p
       LEFT JOIN LATERAL (
         SELECT * FROM printer_status
         WHERE printer_id = p.id
         ORDER BY created_at DESC
         LIMIT 1
       ) ps ON true
       WHERE p.id = $1 AND p.organization_id = $2`,
      [id, organizationId]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Printer not found' });
    }

    res.json(result.rows[0]);
  } catch (error) {
    console.error('Error fetching printer:', error);
    res.status(500).json({ error: 'Failed to fetch printer' });
  }
});

// Update printer
router.put('/:id', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId, ...updates } = req.body;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    // Build UPDATE query
    const allowedFields = [
      'name', 'brand', 'model', 'serial_number',
      'connection_type', 'ip_address', 'access_code',
      'mqtt_broker', 'mqtt_username', 'mqtt_password',
      'num_ams_units', 'max_build_volume_x', 'max_build_volume_y', 'max_build_volume_z',
      'max_nozzle_temp', 'max_bed_temp', 'supported_materials',
      'default_nozzle_size', 'firmware_version', 'location', 'notes', 'is_active'
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
    updateValues.push(organizationId, id);

    const queryText = `
      UPDATE printers
      SET ${updateFields.join(', ')}
      WHERE organization_id = $${paramIndex} AND id = $${paramIndex + 1}
      RETURNING *
    `;

    const result = await query(queryText, updateValues);

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Printer not found' });
    }

    res.json(result.rows[0]);
  } catch (error) {
    console.error('Error updating printer:', error);
    res.status(500).json({ error: 'Failed to update printer' });
  }
});

// Delete printer (soft delete)
router.delete('/:id', authenticate, async (req, res) => {
  try {
    const { id } = req.params;
    const { organizationId } = req.query;

    if (!organizationId) {
      return res.status(400).json({ error: 'organizationId required' });
    }

    const result = await query(
      `UPDATE printers SET is_active = false, updated_at = NOW()
       WHERE id = $1 AND organization_id = $2
       RETURNING *`,
      [id, organizationId]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Printer not found' });
    }

    res.json({ message: 'Printer deleted successfully' });
  } catch (error) {
    console.error('Error deleting printer:', error);
    res.status(500).json({ error: 'Failed to delete printer' });
  }
});

export default router;
