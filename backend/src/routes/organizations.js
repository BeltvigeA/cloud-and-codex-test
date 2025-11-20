import express from 'express';
import { query } from '../db.js';
import { authenticateJWT } from '../middleware/auth.js';

const router = express.Router();

/**
 * Get all organizations for current user
 * GET /api/organizations
 */
router.get('/', authenticateJWT, async (req, res) => {
  try {
    const result = await query(
      `SELECT
        o.id,
        o.name,
        o.description,
        o.logo_url,
        o.website,
        o.industry,
        o.size,
        o.country,
        o.vat_number,
        o.api_key,
        o.api_key_created_at,
        o.api_key_last_used_at,
        o.created_at,
        o.updated_at,
        om.role
      FROM organizations o
      INNER JOIN organization_members om ON o.id = om.organization_id
      WHERE om.user_id = $1 AND o.is_active = true
      ORDER BY o.created_at DESC`,
      [req.user.id]
    );

    res.json({
      data: result.rows
    });
  } catch (error) {
    console.error('Get organizations error:', error);
    res.status(500).json({ error: 'Server error' });
  }
});

/**
 * Get organization by ID
 * GET /api/organizations/:id
 */
router.get('/:id', authenticateJWT, async (req, res) => {
  try {
    const { id } = req.params;

    // Check if user has access to this organization
    const accessCheck = await query(
      `SELECT role FROM organization_members WHERE organization_id = $1 AND user_id = $2`,
      [id, req.user.id]
    );

    if (accessCheck.rows.length === 0) {
      return res.status(403).json({ error: 'Access denied' });
    }

    const result = await query(
      `SELECT
        id,
        name,
        description,
        logo_url,
        website,
        industry,
        size,
        country,
        vat_number,
        api_key,
        api_key_created_at,
        api_key_last_used_at,
        created_at,
        updated_at
      FROM organizations
      WHERE id = $1 AND is_active = true`,
      [id]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Organization not found' });
    }

    res.json({
      data: result.rows[0]
    });
  } catch (error) {
    console.error('Get organization error:', error);
    res.status(500).json({ error: 'Server error' });
  }
});

/**
 * Regenerate API key for organization
 * POST /api/organizations/:id/regenerate-api-key
 */
router.post('/:id/regenerate-api-key', authenticateJWT, async (req, res) => {
  try {
    const { id } = req.params;

    // Check if user has access and is owner/admin
    const accessCheck = await query(
      `SELECT role FROM organization_members WHERE organization_id = $1 AND user_id = $2`,
      [id, req.user.id]
    );

    if (accessCheck.rows.length === 0) {
      return res.status(403).json({ error: 'Access denied' });
    }

    const userRole = accessCheck.rows[0].role;
    if (userRole !== 'owner' && userRole !== 'admin') {
      return res.status(403).json({
        error: 'Only organization owners and admins can regenerate API keys'
      });
    }

    // Generate new API key
    const result = await query(
      `UPDATE organizations
       SET
         api_key = generate_api_key(),
         api_key_created_at = NOW(),
         updated_at = NOW()
       WHERE id = $1
       RETURNING
         id,
         name,
         api_key,
         api_key_created_at`,
      [id]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Organization not found' });
    }

    console.log(`🔑 API key regenerated for organization: ${result.rows[0].name}`);

    res.json({
      data: result.rows[0]
    });
  } catch (error) {
    console.error('Regenerate API key error:', error);
    res.status(500).json({ error: 'Server error' });
  }
});

/**
 * Create new organization
 * POST /api/organizations
 */
router.post('/', authenticateJWT, async (req, res) => {
  try {
    const { name, description, logo_url, website, industry, size, country, vat_number } = req.body;

    if (!name) {
      return res.status(400).json({ error: 'Name is required' });
    }

    // Create organization with auto-generated API key
    const result = await query(
      `INSERT INTO organizations (name, description, logo_url, website, industry, size, country, vat_number, api_key, api_key_created_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, generate_api_key(), NOW())
       RETURNING id, name, api_key, api_key_created_at, created_at`,
      [name, description, logo_url, website, industry, size, country, vat_number]
    );

    const organization = result.rows[0];

    // Add creator as owner
    await query(
      `INSERT INTO organization_members (organization_id, user_id, role)
       VALUES ($1, $2, 'owner')`,
      [organization.id, req.user.id]
    );

    console.log(`✨ Organization created: ${organization.name} by user ${req.user.id}`);

    res.status(201).json({
      data: organization
    });
  } catch (error) {
    console.error('Create organization error:', error);
    res.status(500).json({ error: 'Server error' });
  }
});

/**
 * Update organization
 * PUT /api/organizations/:id
 */
router.put('/:id', authenticateJWT, async (req, res) => {
  try {
    const { id } = req.params;
    const { name, description, logo_url, website, industry, size, country, vat_number } = req.body;

    // Check if user has access and is owner/admin
    const accessCheck = await query(
      `SELECT role FROM organization_members WHERE organization_id = $1 AND user_id = $2`,
      [id, req.user.id]
    );

    if (accessCheck.rows.length === 0) {
      return res.status(403).json({ error: 'Access denied' });
    }

    const userRole = accessCheck.rows[0].role;
    if (userRole !== 'owner' && userRole !== 'admin') {
      return res.status(403).json({
        error: 'Only organization owners and admins can update organization details'
      });
    }

    const result = await query(
      `UPDATE organizations
       SET
         name = COALESCE($1, name),
         description = COALESCE($2, description),
         logo_url = COALESCE($3, logo_url),
         website = COALESCE($4, website),
         industry = COALESCE($5, industry),
         size = COALESCE($6, size),
         country = COALESCE($7, country),
         vat_number = COALESCE($8, vat_number),
         updated_at = NOW()
       WHERE id = $9
       RETURNING id, name, updated_at`,
      [name, description, logo_url, website, industry, size, country, vat_number, id]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Organization not found' });
    }

    res.json({
      data: result.rows[0]
    });
  } catch (error) {
    console.error('Update organization error:', error);
    res.status(500).json({ error: 'Server error' });
  }
});

export default router;
