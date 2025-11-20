import { query } from '../db.js';

/**
 * Middleware to authenticate printer clients using API keys
 *
 * Supports two methods:
 * 1. X-API-Key header
 * 2. apiKey query parameter
 *
 * Sets req.organization with the authenticated organization
 */
export const authenticatePrinterClient = async (req, res, next) => {
  try {
    // Get API key from header or query parameter
    const apiKey = req.headers['x-api-key'] || req.query.apiKey;

    if (!apiKey) {
      return res.status(401).json({
        error: 'API key required',
        message: 'Please provide an API key via X-API-Key header or apiKey query parameter'
      });
    }

    // Validate API key format (should start with pk_)
    if (!apiKey.startsWith('pk_')) {
      return res.status(401).json({
        error: 'Invalid API key format',
        message: 'API key must start with pk_'
      });
    }

    // Look up organization by API key
    const result = await query(
      `SELECT
        id,
        name,
        is_active,
        api_key_created_at
      FROM organizations
      WHERE api_key = $1 AND is_active = true`,
      [apiKey]
    );

    if (result.rows.length === 0) {
      return res.status(401).json({
        error: 'Invalid API key',
        message: 'The provided API key is not valid or has been deactivated'
      });
    }

    const organization = result.rows[0];

    // Update last used timestamp (fire and forget - don't wait for it)
    query(
      `UPDATE organizations SET api_key_last_used_at = NOW() WHERE id = $1`,
      [organization.id]
    ).catch(err => console.error('Failed to update api_key_last_used_at:', err));

    // Attach organization to request
    req.organization = organization;
    req.organizationId = organization.id;

    console.log(`✅ Printer client authenticated: ${organization.name} (${organization.id})`);

    next();
  } catch (error) {
    console.error('Printer authentication error:', error);
    return res.status(500).json({ error: 'Authentication error' });
  }
};
