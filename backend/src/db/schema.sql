-- PrintPro3D Database Schema - Phase 4: Printer Management & Print Queue
-- ========================================================================

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ========================================================================
-- 1. PRINTERS TABLE
-- ========================================================================
CREATE TABLE IF NOT EXISTS printers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,

    -- Printer info
    name VARCHAR(255) NOT NULL,
    brand VARCHAR(100),                    -- Bambu Lab, Prusa, Creality, etc.
    model VARCHAR(100),                    -- X1 Carbon, MK4, Ender 3, etc.
    serial_number VARCHAR(100),

    -- Connection
    connection_type VARCHAR(50) DEFAULT 'network' CHECK (connection_type IN ('network', 'usb', 'cloud', 'mqtt')),
    ip_address VARCHAR(50),
    access_code VARCHAR(100),              -- For Bambu Lab printers
    mqtt_broker VARCHAR(255),
    mqtt_username VARCHAR(100),
    mqtt_password VARCHAR(255),

    -- Capabilities
    num_ams_units INTEGER DEFAULT 0,       -- Antal AMS-enheter (Automatic Material System)
    max_build_volume_x DECIMAL(10,2),
    max_build_volume_y DECIMAL(10,2),
    max_build_volume_z DECIMAL(10,2),
    max_nozzle_temp INTEGER,
    max_bed_temp INTEGER,
    supported_materials TEXT[],            -- ['PLA', 'ABS', 'PETG', 'TPU']

    -- Current status (cached from printer_status)
    current_status VARCHAR(50) DEFAULT 'offline',  -- offline, idle, printing, paused, error
    current_job_id UUID,

    -- Configuration
    default_nozzle_size DECIMAL(5,3) DEFAULT 0.4,
    firmware_version VARCHAR(50),

    -- Location
    location VARCHAR(255),

    -- Metadata
    notes TEXT,
    custom_fields JSONB,

    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    last_seen_at TIMESTAMP,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_by UUID
);

-- Indexes for printers
CREATE INDEX IF NOT EXISTS idx_printers_org ON printers(organization_id);
CREATE INDEX IF NOT EXISTS idx_printers_status ON printers(current_status);
CREATE INDEX IF NOT EXISTS idx_printers_ip ON printers(ip_address);
CREATE INDEX IF NOT EXISTS idx_printers_active ON printers(is_active);

-- ========================================================================
-- 2. PRINT_JOBS TABLE
-- ========================================================================
CREATE TABLE IF NOT EXISTS print_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,

    -- Job info
    product_id UUID NOT NULL,
    printer_id UUID,

    -- Job details
    plates_requested INTEGER DEFAULT 1,
    plates_completed INTEGER DEFAULT 0,
    priority VARCHAR(50) DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),

    -- Status
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN (
        'pending', 'queued', 'claimed', 'preparing', 'printing',
        'paused', 'completed', 'failed', 'cancelled'
    )),

    -- AMS Configuration (Automatic Material System)
    ams_configuration JSONB,               -- Filament slot configuration

    -- Timing
    queued_at TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    estimated_completion_at TIMESTAMP,

    -- Progress tracking
    progress_percentage DECIMAL(5,2) DEFAULT 0,
    current_layer INTEGER,
    total_layers INTEGER,

    -- Fetch token (for printer-agent claiming)
    fetch_token VARCHAR(255) UNIQUE,
    fetch_token_expiry TIMESTAMP,
    claimed_by VARCHAR(255),               -- recipientId som claimed jobben

    -- Results
    actual_print_time_minutes INTEGER,
    actual_filament_used_grams DECIMAL(10,2),
    failure_reason TEXT,

    -- Metadata
    notes TEXT,
    custom_fields JSONB,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_by UUID
);

-- Indexes for print_jobs
CREATE INDEX IF NOT EXISTS idx_print_jobs_org ON print_jobs(organization_id);
CREATE INDEX IF NOT EXISTS idx_print_jobs_status ON print_jobs(status);
CREATE INDEX IF NOT EXISTS idx_print_jobs_printer ON print_jobs(printer_id);
CREATE INDEX IF NOT EXISTS idx_print_jobs_product ON print_jobs(product_id);
CREATE INDEX IF NOT EXISTS idx_print_jobs_fetch_token ON print_jobs(fetch_token);
CREATE INDEX IF NOT EXISTS idx_print_jobs_created ON print_jobs(created_at DESC);

-- ========================================================================
-- 3. PRINTER_STATUS TABLE
-- ========================================================================
CREATE TABLE IF NOT EXISTS printer_status (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,
    printer_id UUID NOT NULL,

    -- Status
    status VARCHAR(50) NOT NULL,           -- idle, printing, paused, error, offline

    -- Current job
    current_job_id UUID,

    -- Progress
    progress_percentage DECIMAL(5,2),
    current_layer INTEGER,
    total_layers INTEGER,
    time_elapsed_seconds INTEGER,
    time_remaining_seconds INTEGER,

    -- Temperatures
    nozzle_temp_current DECIMAL(5,2),
    nozzle_temp_target DECIMAL(5,2),
    bed_temp_current DECIMAL(5,2),
    bed_temp_target DECIMAL(5,2),
    chamber_temp_current DECIMAL(5,2),

    -- Speed and flow
    print_speed_percentage INTEGER,
    flow_rate_percentage INTEGER,

    -- Fan speeds
    part_cooling_fan_speed INTEGER,       -- 0-100%
    aux_fan_speed INTEGER,
    chamber_fan_speed INTEGER,

    -- Error info
    error_code VARCHAR(50),
    error_message TEXT,

    -- Connection
    is_online BOOLEAN DEFAULT TRUE,
    last_update_timestamp TIMESTAMP DEFAULT NOW(),

    -- Metadata
    raw_status_data JSONB,                -- Full raw status from printer

    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for printer_status
CREATE INDEX IF NOT EXISTS idx_printer_status_printer ON printer_status(printer_id);
CREATE INDEX IF NOT EXISTS idx_printer_status_org ON printer_status(organization_id);
CREATE INDEX IF NOT EXISTS idx_printer_status_timestamp ON printer_status(last_update_timestamp);
CREATE INDEX IF NOT EXISTS idx_printer_status_online ON printer_status(is_online);
CREATE INDEX IF NOT EXISTS idx_printer_status_created ON printer_status(created_at DESC);

-- ========================================================================
-- 4. PRINTER_COMMANDS TABLE
-- ========================================================================
CREATE TABLE IF NOT EXISTS printer_commands (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,
    printer_id UUID NOT NULL,

    -- Command targeting
    recipient_id VARCHAR(255) NOT NULL,    -- ID for printer-agent/backend som skal utføre
    printer_ip_address VARCHAR(50),

    -- Command details
    command_type VARCHAR(50) NOT NULL CHECK (command_type IN (
        'start_print', 'pause_print', 'resume_print', 'stop_print', 'cancel_print',
        'set_bed_temp', 'set_nozzle_temp', 'set_chamber_temp',
        'home_all', 'home_x', 'home_y', 'home_z',
        'jog', 'extrude', 'retract',
        'set_fan_speed', 'set_print_speed', 'set_flow_rate',
        'camera_on', 'camera_off',
        'light_on', 'light_off',
        'load_filament', 'unload_filament',
        'calibrate_bed', 'calibrate_z_offset',
        'custom_gcode'
    )),

    metadata JSONB,                        -- Command-specific parameters

    -- Status
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'executing', 'completed', 'failed', 'timeout')),

    -- Results
    result TEXT,
    error_message TEXT,

    -- Timing
    sent_at TIMESTAMP,
    completed_at TIMESTAMP,
    timeout_at TIMESTAMP,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_by UUID
);

-- Indexes for printer_commands
CREATE INDEX IF NOT EXISTS idx_printer_commands_printer ON printer_commands(printer_id);
CREATE INDEX IF NOT EXISTS idx_printer_commands_org ON printer_commands(organization_id);
CREATE INDEX IF NOT EXISTS idx_printer_commands_recipient ON printer_commands(recipient_id);
CREATE INDEX IF NOT EXISTS idx_printer_commands_status ON printer_commands(status);
CREATE INDEX IF NOT EXISTS idx_printer_commands_created ON printer_commands(created_at);

-- ========================================================================
-- 5. UPDATE USER_SETTINGS (if table exists)
-- ========================================================================
-- Check if user_settings table exists and add printer-related columns
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user_settings') THEN
        ALTER TABLE user_settings
            ADD COLUMN IF NOT EXISTS printer_backend_url VARCHAR(255),
            ADD COLUMN IF NOT EXISTS default_recipient_id VARCHAR(255);

        -- Set default for existing records
        UPDATE user_settings
        SET printer_backend_url = 'https://printer-backend-934564650450.europe-west1.run.app'
        WHERE printer_backend_url IS NULL;
    END IF;
END $$;

-- ========================================================================
-- FOREIGN KEY CONSTRAINTS (Add if referenced tables exist)
-- ========================================================================
-- These would be added once we know the complete schema
-- For now, commented out to allow independent testing

-- ALTER TABLE print_jobs
--     ADD CONSTRAINT fk_print_jobs_printer
--     FOREIGN KEY (printer_id) REFERENCES printers(id) ON DELETE SET NULL;

-- ALTER TABLE print_jobs
--     ADD CONSTRAINT fk_print_jobs_product
--     FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE;

-- ALTER TABLE printer_status
--     ADD CONSTRAINT fk_printer_status_printer
--     FOREIGN KEY (printer_id) REFERENCES printers(id) ON DELETE CASCADE;

-- ALTER TABLE printer_commands
--     ADD CONSTRAINT fk_printer_commands_printer
--     FOREIGN KEY (printer_id) REFERENCES printers(id) ON DELETE CASCADE;

-- ========================================================================
-- CLEANUP FUNCTION (Optional - for maintaining printer_status table)
-- ========================================================================
-- Function to keep only last 1000 status records per printer
CREATE OR REPLACE FUNCTION cleanup_old_printer_status()
RETURNS void AS $$
BEGIN
    DELETE FROM printer_status
    WHERE id IN (
        SELECT id
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY printer_id ORDER BY created_at DESC) as rn
            FROM printer_status
        ) t
        WHERE t.rn > 1000
    );
END;
$$ LANGUAGE plpgsql;

-- ========================================================================
-- VIEWS (Optional - for easier querying)
-- ========================================================================

-- View: Active printers with latest status
CREATE OR REPLACE VIEW v_printers_with_status AS
SELECT
    p.*,
    ps.status as live_status,
    ps.progress_percentage as live_progress,
    ps.nozzle_temp_current,
    ps.bed_temp_current,
    ps.time_remaining_seconds,
    ps.last_update_timestamp
FROM printers p
LEFT JOIN LATERAL (
    SELECT * FROM printer_status
    WHERE printer_id = p.id
    ORDER BY created_at DESC
    LIMIT 1
) ps ON true
WHERE p.is_active = true;

-- View: Active print jobs with details
CREATE OR REPLACE VIEW v_active_print_jobs AS
SELECT
    pj.*,
    p.name as printer_name,
    p.model as printer_model,
    p.ip_address as printer_ip
FROM print_jobs pj
LEFT JOIN printers p ON pj.printer_id = p.id
WHERE pj.status IN ('pending', 'queued', 'printing', 'paused')
ORDER BY
    CASE pj.priority
        WHEN 'urgent' THEN 1
        WHEN 'high' THEN 2
        WHEN 'normal' THEN 3
        WHEN 'low' THEN 4
    END,
    pj.created_at;

-- ========================================================================
-- COMMENTS (Documentation)
-- ========================================================================
COMMENT ON TABLE printers IS 'Stores registered 3D printers with their configuration and capabilities';
COMMENT ON TABLE print_jobs IS 'Print job queue and history with status tracking';
COMMENT ON TABLE printer_status IS 'Real-time printer status updates (keep last 1000 per printer)';
COMMENT ON TABLE printer_commands IS 'Command queue for printer-agent to poll and execute';

-- ========================================================================
-- PHASE 5: POINT OF SALE (POS) SYSTEM
-- ========================================================================

-- ========================================================================
-- 6. POS_ORDERS TABLE
-- ========================================================================
CREATE TABLE IF NOT EXISTS pos_orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,

    -- Status
    status VARCHAR(50) DEFAULT 'Open' CHECK (status IN ('Open', 'Paid', 'Cancelled', 'Refunded')),

    -- Customer info (optional)
    customer_name VARCHAR(255),
    customer_email VARCHAR(255),
    customer_phone VARCHAR(50),

    -- Totals
    subtotal DECIMAL(10,2) NOT NULL DEFAULT 0,
    discount_amount DECIMAL(10,2) DEFAULT 0,
    discount_percentage DECIMAL(5,2) DEFAULT 0,
    tax_amount DECIMAL(10,2) DEFAULT 0,
    tax_percentage DECIMAL(5,2) DEFAULT 0,
    total_amount DECIMAL(10,2) NOT NULL DEFAULT 0,

    -- Payment
    payment_method VARCHAR(50) CHECK (payment_method IN ('Cash', 'Stripe', 'MobilePay', 'Vipps', 'Invoice')),
    payment_status VARCHAR(50) DEFAULT 'pending' CHECK (payment_status IN ('pending', 'processing', 'completed', 'failed', 'refunded')),
    payment_intent_id VARCHAR(255),        -- Stripe Payment Intent ID
    payment_reference VARCHAR(255),        -- Vipps/MobilePay reference
    amount_paid DECIMAL(10,2),
    amount_due DECIMAL(10,2),
    change_given DECIMAL(10,2),

    -- Timestamps
    sale_timestamp TIMESTAMP,
    paid_at TIMESTAMP,
    refunded_at TIMESTAMP,

    -- Receipt
    receipt_number VARCHAR(100),           -- Unique receipt number (auto-generated)
    receipt_pdf_path TEXT,                 -- Path to PDF receipt in Cloud Storage

    -- Notes
    note TEXT,
    internal_notes TEXT,

    -- Finance integration
    finance_income_id UUID,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_by UUID
);

-- Indexes for pos_orders
CREATE INDEX IF NOT EXISTS idx_pos_orders_org ON pos_orders(organization_id);
CREATE INDEX IF NOT EXISTS idx_pos_orders_status ON pos_orders(status);
CREATE INDEX IF NOT EXISTS idx_pos_orders_payment_status ON pos_orders(payment_status);
CREATE INDEX IF NOT EXISTS idx_pos_orders_receipt_number ON pos_orders(receipt_number);
CREATE INDEX IF NOT EXISTS idx_pos_orders_sale_timestamp ON pos_orders(sale_timestamp);

-- Create sequence for receipt numbers
CREATE SEQUENCE IF NOT EXISTS receipt_number_seq START 1;

-- Auto-generate receipt number trigger
CREATE OR REPLACE FUNCTION generate_receipt_number()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.receipt_number IS NULL THEN
        NEW.receipt_number := 'REC-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' || LPAD(NEXTVAL('receipt_number_seq')::TEXT, 6, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger
DROP TRIGGER IF EXISTS set_receipt_number ON pos_orders;
CREATE TRIGGER set_receipt_number
    BEFORE INSERT ON pos_orders
    FOR EACH ROW
    EXECUTE FUNCTION generate_receipt_number();

-- ========================================================================
-- 7. POS_ORDER_LINES TABLE
-- ========================================================================
CREATE TABLE IF NOT EXISTS pos_order_lines (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,
    order_id UUID NOT NULL,

    -- Product reference
    product_id UUID NOT NULL,
    product_name VARCHAR(255) NOT NULL,   -- Snapshot of product name at sale time
    product_sku VARCHAR(100),

    -- Quantity and pricing
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price DECIMAL(10,2) NOT NULL,
    discount_amount DECIMAL(10,2) DEFAULT 0,
    tax_percentage DECIMAL(5,2) DEFAULT 0,
    line_total DECIMAL(10,2) NOT NULL,

    -- Stock handling
    allow_backorder BOOLEAN DEFAULT FALSE,
    allow_backorder_note TEXT,
    stock_deducted BOOLEAN DEFAULT FALSE,

    -- Timestamps
    sale_timestamp TIMESTAMP,

    created_at TIMESTAMP DEFAULT NOW(),
    created_by UUID
);

-- Indexes for pos_order_lines
CREATE INDEX IF NOT EXISTS idx_pos_order_lines_org ON pos_order_lines(organization_id);
CREATE INDEX IF NOT EXISTS idx_pos_order_lines_order ON pos_order_lines(order_id);
CREATE INDEX IF NOT EXISTS idx_pos_order_lines_product ON pos_order_lines(product_id);

-- ========================================================================
-- 8. PAYMENT_SETTINGS TABLE
-- ========================================================================
CREATE TABLE IF NOT EXISTS payment_settings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,

    -- Stripe
    stripe_enabled BOOLEAN DEFAULT FALSE,
    stripe_publishable_key VARCHAR(255),
    stripe_secret_key VARCHAR(255),
    stripe_webhook_secret VARCHAR(255),

    -- MobilePay
    mobile_pay_enabled BOOLEAN DEFAULT FALSE,
    mobile_pay_merchant_id VARCHAR(255),
    mobile_pay_api_key VARCHAR(255),

    -- Vipps
    vipps_enabled BOOLEAN DEFAULT FALSE,
    vipps_client_id VARCHAR(255),
    vipps_client_secret VARCHAR(255),
    vipps_subscription_key VARCHAR(255),
    vipps_number VARCHAR(50),
    vipps_qr_code_url TEXT,

    -- Default settings
    default_payment_method VARCHAR(50) DEFAULT 'Cash',
    require_customer_info BOOLEAN DEFAULT FALSE,

    -- Tax and currency
    country VARCHAR(2) CHECK (country IN ('NO', 'SE', 'DK', 'FI')),
    currency VARCHAR(3) CHECK (currency IN ('NOK', 'SEK', 'DKK', 'EUR')),
    default_tax_percentage DECIMAL(5,2) DEFAULT 25.00,

    -- Receipt settings
    company_logo_url TEXT,
    receipt_footer_text TEXT,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_by UUID,

    UNIQUE(organization_id)
);

-- Index for payment_settings
CREATE INDEX IF NOT EXISTS idx_payment_settings_org ON payment_settings(organization_id);

-- ========================================================================
-- 9. STOCK_TRANSACTIONS TABLE (if not exists from Phase 3)
-- ========================================================================
CREATE TABLE IF NOT EXISTS stock_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,
    product_id UUID NOT NULL,
    stock_product_id UUID,

    -- Transaction
    transaction_type VARCHAR(50) NOT NULL CHECK (transaction_type IN (
        'initial', 'restock', 'sale', 'print', 'adjustment', 'return', 'damaged', 'transfer'
    )),
    quantity_change INTEGER NOT NULL,
    quantity_before INTEGER NOT NULL,
    quantity_after INTEGER NOT NULL,

    -- Reference
    reference_type VARCHAR(50),            -- pos_order, print_job, etc.
    reference_id UUID,
    reason TEXT,

    -- Metadata
    performed_by UUID,
    transaction_date TIMESTAMP DEFAULT NOW(),
    notes TEXT,

    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for stock_transactions
CREATE INDEX IF NOT EXISTS idx_stock_trans_org ON stock_transactions(organization_id);
CREATE INDEX IF NOT EXISTS idx_stock_trans_product ON stock_transactions(product_id);
CREATE INDEX IF NOT EXISTS idx_stock_trans_reference ON stock_transactions(reference_type, reference_id);
CREATE INDEX IF NOT EXISTS idx_stock_trans_date ON stock_transactions(transaction_date);

-- ========================================================================
-- 10. PRODUCTS TABLE (Basic structure if not exists)
-- ========================================================================
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,

    -- Product info
    name VARCHAR(255) NOT NULL,
    sku VARCHAR(100),
    description TEXT,

    -- Pricing
    price DECIMAL(10,2),
    cost DECIMAL(10,2),

    -- Stock
    stock_tracked BOOLEAN DEFAULT TRUE,

    -- Metadata
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_by UUID
);

CREATE INDEX IF NOT EXISTS idx_products_org ON products(organization_id);
CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);

-- ========================================================================
-- 11. STOCK_PRODUCTS TABLE (if not exists from Phase 3)
-- ========================================================================
CREATE TABLE IF NOT EXISTS stock_products (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,
    product_id UUID NOT NULL,

    -- Stock levels
    quantity INTEGER DEFAULT 0,
    min_stock INTEGER DEFAULT 0,

    -- Status
    status VARCHAR(50) DEFAULT 'in_stock' CHECK (status IN ('in_stock', 'low_stock', 'out_of_stock')),

    -- Timestamps
    last_movement_at TIMESTAMP,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stock_products_org ON stock_products(organization_id);
CREATE INDEX IF NOT EXISTS idx_stock_products_product ON stock_products(product_id);
CREATE INDEX IF NOT EXISTS idx_stock_products_status ON stock_products(status);

-- ========================================================================
-- 12. FINANCE_INCOMES TABLE (Basic structure if not exists)
-- ========================================================================
CREATE TABLE IF NOT EXISTS finance_incomes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,

    -- Income details
    date DATE NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    vat_pct DECIMAL(5,2),
    category VARCHAR(100),
    customer VARCHAR(255),
    notes TEXT,

    -- POS integration
    pos_order_id UUID,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_by UUID
);

CREATE INDEX IF NOT EXISTS idx_finance_incomes_org ON finance_incomes(organization_id);
CREATE INDEX IF NOT EXISTS idx_finance_incomes_date ON finance_incomes(date);
CREATE INDEX IF NOT EXISTS idx_finance_incomes_pos_order ON finance_incomes(pos_order_id);

-- ========================================================================
-- 13. ORGANIZATIONS TABLE (Basic structure if not exists)
-- ========================================================================
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ========================================================================
-- 14. ORGANIZATION_MEMBERS TABLE (if not exists)
-- ========================================================================
CREATE TABLE IF NOT EXISTS organization_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,
    user_id UUID NOT NULL,
    role VARCHAR(50) DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_org_members_org ON organization_members(organization_id);
CREATE INDEX IF NOT EXISTS idx_org_members_user ON organization_members(user_id);

-- ========================================================================
-- 15. USERS TABLE (Basic structure if not exists)
-- ========================================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ========================================================================
-- COMMENTS FOR POS TABLES
-- ========================================================================
COMMENT ON TABLE pos_orders IS 'Point of Sale orders with payment and receipt tracking';
COMMENT ON TABLE pos_order_lines IS 'Line items for POS orders';
COMMENT ON TABLE payment_settings IS 'Payment gateway settings per organization';
COMMENT ON TABLE stock_transactions IS 'Stock movement history for audit trail';

-- ========================================================================
-- END OF SCHEMA
-- ========================================================================
