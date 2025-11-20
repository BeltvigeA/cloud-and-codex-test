# API Key Authentication Implementation

This document describes the API key authentication system implemented for printer clients in PrintPro3D.

## Overview

The API key authentication system provides secure access control for printer clients connecting to the PrintPro3D backend. Each organization receives a unique API key that printer clients use to authenticate their requests.

## Architecture

### Database Schema

The system adds three new columns to the `organizations` table:

- `api_key` (VARCHAR(64), UNIQUE): The API key with format `pk_XXXXX...`
- `api_key_created_at` (TIMESTAMP): When the API key was created/regenerated
- `api_key_last_used_at` (TIMESTAMP): Last time the API key was used for authentication

A PostgreSQL function `generate_api_key()` creates cryptographically secure API keys using the pattern:
- Prefix: `pk_`
- Random part: 54 characters (base62: a-z, A-Z, 0-9)

### Backend Components

#### 1. Database Migration
**File**: `backend/migrations/add-organization-api-keys.sql`

- Adds API key columns to organizations table
- Creates `generate_api_key()` function
- Generates API keys for existing organizations
- Creates index for fast API key lookups

**To run the migration**:
```bash
psql -h localhost -U postgres -d printpro3d -f backend/migrations/add-organization-api-keys.sql
```

#### 2. Printer Authentication Middleware
**File**: `backend/src/middleware/printerAuth.js`

Authenticates printer clients using API keys. Supports two authentication methods:
1. `X-API-Key` HTTP header
2. `apiKey` query parameter

**Workflow**:
1. Extracts API key from header or query parameter
2. Validates API key format (must start with `pk_`)
3. Looks up organization by API key
4. Verifies organization is active
5. Updates `api_key_last_used_at` timestamp
6. Attaches organization info to `req.organization` and `req.organizationId`

**Usage in routes**:
```javascript
import { authenticatePrinterClient } from '../middleware/printerAuth.js';

router.post('/printer-status', authenticatePrinterClient, async (req, res) => {
  const organizationId = req.organizationId; // Set by middleware
  // ...
});
```

#### 3. Organizations Route Updates
**File**: `backend/src/routes/organizations.js`

**New endpoint**: `POST /api/organizations/:id/regenerate-api-key`
- Requires authentication via JWT
- Requires user to be organization owner or admin
- Generates new API key using `generate_api_key()`
- Returns new API key and creation timestamp

**Updated endpoints**:
- `GET /api/organizations` - Now includes `api_key`, `api_key_created_at`, `api_key_last_used_at`
- `GET /api/organizations/:id` - Now includes API key fields

#### 4. Printer Status Route Updates
**File**: `backend/src/routes/printer-status.js`

**Updated**: `POST /api/printer-status`
- Now uses `authenticatePrinterClient` middleware
- Gets `organizationId` from middleware (via `req.organizationId`)
- Removes `organizationId` from request body requirements
- Auto-registers new printers for authenticated organization

**New**: `GET /api/printer-status/:serial`
- Retrieves printer status using API key authentication
- Scoped to authenticated organization

### Frontend Components

#### 1. API Client
**File**: `src/api/apiClient.js`

**New method**: `organizationAPI.regenerateApiKey(organizationId)`
- Makes POST request to `/api/organizations/:id/regenerate-api-key`
- Returns new API key data

**New API**: `printerStatusAPI`
- `update(data, apiKey)` - Update printer status with API key
- `get(serial, apiKey)` - Get printer status with API key

#### 2. API Entities
**File**: `src/api/entities.js`

**Updated**: `Organization` class
- New method: `regenerateApiKey()` - Regenerates organization's API key

#### 3. Printer Connection Settings UI
**File**: `src/components/settings/PrinterConnectionSettings.jsx`

A comprehensive React component for managing printer client authentication:

**Features**:
- Displays organization API key (masked by default)
- Show/hide API key toggle
- Copy API key to clipboard
- Regenerate API key with confirmation
- Manage recipient ID for printer agents
- Display connection URLs and example configuration

**UI Components**:
- `Card`, `CardHeader`, `CardTitle`, `CardContent` - Card layouts
- `Input` - Text inputs
- `Button` - Action buttons
- `Label` - Form labels
- `Alert`, `AlertDescription` - Warning and info messages
- Icons from `lucide-react`: `Info`, `Copy`, `RefreshCw`, `Eye`, `EyeOff`, `Check`

## Security Considerations

### API Key Security
- API keys start with `pk_` prefix for easy identification
- Keys are 64 characters long with high entropy
- Keys are stored as plain text in the database (consider encryption for production)
- Keys should never be exposed in client-side code or logs
- The UI warns users about the sensitivity of API keys

### Authentication Flow
1. Printer client includes API key in request (header or query param)
2. Middleware validates API key format
3. Database lookup verifies key belongs to active organization
4. Last used timestamp updated (non-blocking)
5. Organization context attached to request

### Permission Checks
- Only organization owners and admins can regenerate API keys
- API key provides full access to organization's printer operations
- Each printer is scoped to its organization

### Best Practices
- ⚠️ **Never** commit API keys to version control
- ⚠️ **Never** log API keys in application logs
- ⚠️ Always use HTTPS in production
- Implement rate limiting on printer-status endpoint
- Monitor `api_key_last_used_at` for suspicious activity
- Rotate API keys periodically
- Revoke API keys immediately if compromised

## Usage Examples

### Printer Client Configuration

**Environment variables** (.env file):
```bash
PRINTER_BACKEND_API_KEY=pk_abcdefghijklmnopqrstuvwxyz0123456789
PRINTER_BACKEND_BASE_URL=https://printpro3d-api-931368217793.europe-west1.run.app
BASE44_RECIPIENT_ID=user-123
```

**Command-line arguments**:
```bash
python -m client.client listen \
  --apiKey pk_abcdefghijklmnopqrstuvwxyz0123456789 \
  --baseUrl https://printpro3d-api-931368217793.europe-west1.run.app \
  --recipientId user-123
```

### API Requests

**Using X-API-Key header** (recommended):
```bash
curl -X POST https://api.printpro3d.com/api/printer-status \
  -H "X-API-Key: pk_abcdefghijklmnopqrstuvwxyz0123456789" \
  -H "Content-Type: application/json" \
  -d '{
    "printerSerial": "PRINTER001",
    "status": "printing",
    "progress": 45
  }'
```

**Using query parameter** (fallback):
```bash
curl -X POST "https://api.printpro3d.com/api/printer-status?apiKey=pk_abcdefghijklmnopqrstuvwxyz0123456789" \
  -H "Content-Type: application/json" \
  -d '{
    "printerSerial": "PRINTER001",
    "status": "printing",
    "progress": 45
  }'
```

**Regenerate API key** (requires JWT authentication):
```bash
curl -X POST https://api.printpro3d.com/api/organizations/org-123/regenerate-api-key \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json"
```

Response:
```json
{
  "data": {
    "id": "org-123",
    "name": "Acme Corp",
    "api_key": "pk_NEW_API_KEY_HERE",
    "api_key_created_at": "2025-11-20T10:30:00Z"
  }
}
```

## Testing

### Backend Testing

**1. Test database migration**:
```bash
psql -h localhost -U postgres -d printpro3d -c "SELECT id, name, api_key FROM organizations LIMIT 5;"
```

**2. Test API key authentication**:
```bash
# Get API key from database
API_KEY="pk_abc123..."

# Test printer status endpoint
curl -X POST http://localhost:8080/api/printer-status \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "printerSerial": "TEST123",
    "status": "idle"
  }'
```

Expected response:
```json
{
  "success": true,
  "data": {
    "id": "printer-id",
    "serial_number": "TEST123",
    "status": "idle",
    "progress": null
  }
}
```

**3. Test API key regeneration**:
```bash
# Login first and get JWT token
TOKEN="your-jwt-token"
ORG_ID="your-org-id"

curl -X POST http://localhost:8080/api/organizations/$ORG_ID/regenerate-api-key \
  -H "Authorization: Bearer $TOKEN"
```

### Frontend Testing

1. Log in to the application
2. Navigate to Settings → Printer Connection
3. Verify API key displays (masked)
4. Test show/hide toggle
5. Test copy button
6. Test regenerate button (confirm dialog appears)
7. Verify example code shows correct API key and Recipient ID

### Error Cases to Test

**Invalid API key format**:
```bash
curl -X POST http://localhost:8080/api/printer-status \
  -H "X-API-Key: invalid-key" \
  -H "Content-Type: application/json" \
  -d '{"printerSerial": "TEST123"}'
```

Expected: `401 Unauthorized` with message "API key must start with pk_"

**Missing API key**:
```bash
curl -X POST http://localhost:8080/api/printer-status \
  -H "Content-Type: application/json" \
  -d '{"printerSerial": "TEST123"}'
```

Expected: `401 Unauthorized` with message "API key required"

**Invalid/revoked API key**:
```bash
curl -X POST http://localhost:8080/api/printer-status \
  -H "X-API-Key: pk_nonexistent" \
  -H "Content-Type: application/json" \
  -d '{"printerSerial": "TEST123"}'
```

Expected: `401 Unauthorized` with message "The provided API key is not valid or has been deactivated"

## Troubleshooting

### Database Issues
- **Migration fails**: Check that PostgreSQL extensions are installed (`gen_random_bytes` requires `pgcrypto`)
- **Duplicate key error**: API keys are unique; regenerate if collision occurs (extremely unlikely)
- **Missing columns**: Verify migration ran successfully with `\d organizations`

### Authentication Issues
- **401 Unauthorized**: Check API key format, ensure it starts with `pk_`
- **403 Forbidden**: Verify organization is active (`is_active = true`)
- **Invalid API key**: Key may have been regenerated; check `api_key_created_at`

### Frontend Issues
- **API key not loading**: Check browser console, verify Organization entity returns `api_key` field
- **Regenerate fails**: Ensure user has owner/admin role, check network tab for error details
- **UI not rendering**: Verify all UI components are properly imported

## Future Enhancements

1. **API Key Encryption**: Encrypt API keys at rest in the database
2. **Multiple API Keys**: Allow multiple API keys per organization for key rotation
3. **Scoped API Keys**: Create keys with limited permissions (read-only, specific printers)
4. **API Key Expiration**: Add expiration dates to API keys
5. **Rate Limiting**: Implement rate limiting per API key
6. **Audit Log**: Track all API key usage and regeneration events
7. **Webhook Notifications**: Notify admins when API keys are regenerated
8. **API Key Metadata**: Add labels, descriptions, and creation source to API keys

## Migration Checklist

When deploying to production:

- [ ] Run database migration on production database
- [ ] Verify all existing organizations have API keys
- [ ] Update printer client code to use API key authentication
- [ ] Deploy backend changes with new middleware
- [ ] Deploy frontend changes with new UI
- [ ] Test API key authentication with production printers
- [ ] Notify organization admins about new API key feature
- [ ] Provide documentation for updating printer clients
- [ ] Set up monitoring for API key usage
- [ ] Configure alerts for authentication failures

## Support

For issues or questions:
- Check logs: `console.log` statements in middleware show authentication flow
- Database queries: Monitor `organizations` table for API key updates
- Frontend debugging: Use browser DevTools to inspect API requests
- Backend debugging: Check Express middleware execution order

## References

- [Express.js Middleware](https://expressjs.com/en/guide/using-middleware.html)
- [PostgreSQL gen_random_bytes](https://www.postgresql.org/docs/current/pgcrypto.html)
- [API Key Best Practices](https://cloud.google.com/docs/authentication/api-keys)
