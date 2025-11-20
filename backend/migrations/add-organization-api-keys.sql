-- Add API key column to organizations table
ALTER TABLE organizations
ADD COLUMN IF NOT EXISTS api_key VARCHAR(64) UNIQUE,
ADD COLUMN IF NOT EXISTS api_key_created_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS api_key_last_used_at TIMESTAMP;

-- Create function to generate secure API keys
CREATE OR REPLACE FUNCTION generate_api_key()
RETURNS VARCHAR(64) AS $$
DECLARE
  key_prefix VARCHAR(10) := 'pk_';
  random_part VARCHAR(54);
BEGIN
  -- Generate 54 random characters (base62: a-z, A-Z, 0-9)
  random_part := encode(gen_random_bytes(40), 'base64');
  random_part := regexp_replace(random_part, '[^a-zA-Z0-9]', '', 'g');
  random_part := substring(random_part, 1, 54);

  RETURN key_prefix || random_part;
END;
$$ LANGUAGE plpgsql;

-- Generate API keys for existing organizations
UPDATE organizations
SET
  api_key = generate_api_key(),
  api_key_created_at = NOW()
WHERE api_key IS NULL;

-- Create index for fast API key lookup
CREATE INDEX IF NOT EXISTS idx_organizations_api_key ON organizations(api_key);

-- Add comments
COMMENT ON COLUMN organizations.api_key IS 'API key for printer client authentication';
COMMENT ON COLUMN organizations.api_key_last_used_at IS 'Last time this API key was used';
