-- ========================================================================
-- PHASE 5 MIGRATION: Add missing columns to existing POS tables
-- ========================================================================

-- Add missing columns to pos_orders table
ALTER TABLE pos_orders
    ADD COLUMN IF NOT EXISTS customer_name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS customer_email VARCHAR(255),
    ADD COLUMN IF NOT EXISTS customer_phone VARCHAR(50),
    ADD COLUMN IF NOT EXISTS subtotal DECIMAL(10,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS discount_amount DECIMAL(10,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS discount_percentage DECIMAL(5,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tax_amount DECIMAL(10,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tax_percentage DECIMAL(5,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_amount DECIMAL(10,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS payment_status VARCHAR(50) DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS payment_intent_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS payment_reference VARCHAR(255),
    ADD COLUMN IF NOT EXISTS amount_paid DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS amount_due DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS change_given DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS receipt_number VARCHAR(100),
    ADD COLUMN IF NOT EXISTS receipt_pdf_path TEXT,
    ADD COLUMN IF NOT EXISTS internal_notes TEXT,
    ADD COLUMN IF NOT EXISTS finance_income_id UUID;

-- Update status constraint to include 'Refunded'
ALTER TABLE pos_orders DROP CONSTRAINT IF EXISTS pos_orders_status_check;
ALTER TABLE pos_orders
    ADD CONSTRAINT pos_orders_status_check
    CHECK (status IN ('Open', 'Paid', 'Cancelled', 'Refunded'));

-- Add payment_status constraint
ALTER TABLE pos_orders DROP CONSTRAINT IF EXISTS pos_orders_payment_status_check;
ALTER TABLE pos_orders
    ADD CONSTRAINT pos_orders_payment_status_check
    CHECK (payment_status IN ('pending', 'processing', 'completed', 'failed', 'refunded'));

-- Update payment_method constraint to include 'Invoice'
ALTER TABLE pos_orders DROP CONSTRAINT IF EXISTS pos_orders_payment_method_check;
ALTER TABLE pos_orders
    ADD CONSTRAINT pos_orders_payment_method_check
    CHECK (payment_method IN ('Cash', 'Stripe', 'MobilePay', 'Vipps', 'Invoice'));

-- Add indexes for new columns
CREATE INDEX IF NOT EXISTS idx_pos_orders_payment_status ON pos_orders(payment_status);
CREATE INDEX IF NOT EXISTS idx_pos_orders_receipt_number ON pos_orders(receipt_number);
CREATE INDEX IF NOT EXISTS idx_pos_orders_sale_timestamp ON pos_orders(sale_timestamp);

-- Create sequence for receipt numbers if not exists
CREATE SEQUENCE IF NOT EXISTS receipt_number_seq START 1;

-- Create or replace receipt number generation function
CREATE OR REPLACE FUNCTION generate_receipt_number()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.receipt_number IS NULL THEN
        NEW.receipt_number := 'REC-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' || LPAD(NEXTVAL('receipt_number_seq')::TEXT, 6, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for receipt number generation
DROP TRIGGER IF EXISTS set_receipt_number ON pos_orders;
CREATE TRIGGER set_receipt_number
    BEFORE INSERT ON pos_orders
    FOR EACH ROW
    EXECUTE FUNCTION generate_receipt_number();

-- Add missing columns to pos_order_lines table if needed
ALTER TABLE pos_order_lines
    ADD COLUMN IF NOT EXISTS product_name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS product_sku VARCHAR(100),
    ADD COLUMN IF NOT EXISTS quantity INTEGER,
    ADD COLUMN IF NOT EXISTS unit_price DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS discount_amount DECIMAL(10,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tax_percentage DECIMAL(5,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS line_total DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS allow_backorder BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS allow_backorder_note TEXT,
    ADD COLUMN IF NOT EXISTS stock_deducted BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS sale_timestamp TIMESTAMP;

-- Add quantity constraint if not exists
DO $$
BEGIN
    ALTER TABLE pos_order_lines
        ADD CONSTRAINT pos_order_lines_quantity_check CHECK (quantity > 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Add indexes for pos_order_lines
CREATE INDEX IF NOT EXISTS idx_pos_order_lines_product ON pos_order_lines(product_id);

-- Update payment_settings table with new columns
ALTER TABLE payment_settings
    ADD COLUMN IF NOT EXISTS stripe_enabled BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS stripe_publishable_key VARCHAR(255),
    ADD COLUMN IF NOT EXISTS stripe_secret_key VARCHAR(255),
    ADD COLUMN IF NOT EXISTS stripe_webhook_secret VARCHAR(255),
    ADD COLUMN IF NOT EXISTS mobile_pay_enabled BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS mobile_pay_merchant_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS mobile_pay_api_key VARCHAR(255),
    ADD COLUMN IF NOT EXISTS vipps_enabled BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS vipps_client_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS vipps_client_secret VARCHAR(255),
    ADD COLUMN IF NOT EXISTS vipps_subscription_key VARCHAR(255),
    ADD COLUMN IF NOT EXISTS vipps_number VARCHAR(50),
    ADD COLUMN IF NOT EXISTS vipps_qr_code_url TEXT,
    ADD COLUMN IF NOT EXISTS default_payment_method VARCHAR(50) DEFAULT 'Cash',
    ADD COLUMN IF NOT EXISTS require_customer_info BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS country VARCHAR(2),
    ADD COLUMN IF NOT EXISTS currency VARCHAR(3),
    ADD COLUMN IF NOT EXISTS default_tax_percentage DECIMAL(5,2) DEFAULT 25.00,
    ADD COLUMN IF NOT EXISTS company_logo_url TEXT,
    ADD COLUMN IF NOT EXISTS receipt_footer_text TEXT;

-- Add constraints to payment_settings
ALTER TABLE payment_settings DROP CONSTRAINT IF EXISTS payment_settings_country_check;
ALTER TABLE payment_settings
    ADD CONSTRAINT payment_settings_country_check
    CHECK (country IN ('NO', 'SE', 'DK', 'FI'));

ALTER TABLE payment_settings DROP CONSTRAINT IF EXISTS payment_settings_currency_check;
ALTER TABLE payment_settings
    ADD CONSTRAINT payment_settings_currency_check
    CHECK (currency IN ('NOK', 'SEK', 'DKK', 'EUR'));

-- Add unique constraint on organization_id for payment_settings
CREATE UNIQUE INDEX IF NOT EXISTS payment_settings_org_unique ON payment_settings(organization_id);

-- Update finance_incomes to add pos_order_id if not exists
ALTER TABLE finance_incomes
    ADD COLUMN IF NOT EXISTS pos_order_id UUID;

-- Add index for pos_order_id
CREATE INDEX IF NOT EXISTS idx_finance_incomes_pos_order ON finance_incomes(pos_order_id);

-- Add foreign key constraint if not exists
DO $$
BEGIN
    ALTER TABLE finance_incomes
        ADD CONSTRAINT finance_incomes_pos_order_fkey
        FOREIGN KEY (pos_order_id) REFERENCES pos_orders(id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Ensure stock_transactions table exists with all required columns
CREATE TABLE IF NOT EXISTS stock_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL,
    product_id UUID NOT NULL,
    stock_product_id UUID,
    transaction_type VARCHAR(50) NOT NULL CHECK (transaction_type IN (
        'initial', 'restock', 'sale', 'print', 'adjustment', 'return', 'damaged', 'transfer'
    )),
    quantity_change INTEGER NOT NULL,
    quantity_before INTEGER NOT NULL,
    quantity_after INTEGER NOT NULL,
    reference_type VARCHAR(50),
    reference_id UUID,
    reason TEXT,
    performed_by UUID,
    transaction_date TIMESTAMP DEFAULT NOW(),
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Add indexes for stock_transactions if not exists
CREATE INDEX IF NOT EXISTS idx_stock_trans_org ON stock_transactions(organization_id);
CREATE INDEX IF NOT EXISTS idx_stock_trans_product ON stock_transactions(product_id);
CREATE INDEX IF NOT EXISTS idx_stock_trans_reference ON stock_transactions(reference_type, reference_id);
CREATE INDEX IF NOT EXISTS idx_stock_trans_date ON stock_transactions(transaction_date);

-- Update products table to ensure stock_tracked column exists
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS stock_tracked BOOLEAN DEFAULT TRUE;

-- Update stock_products to ensure all required columns exist
ALTER TABLE stock_products
    ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'in_stock',
    ADD COLUMN IF NOT EXISTS last_movement_at TIMESTAMP;

-- Add status constraint
ALTER TABLE stock_products DROP CONSTRAINT IF EXISTS stock_products_status_check;
ALTER TABLE stock_products
    ADD CONSTRAINT stock_products_status_check
    CHECK (status IN ('in_stock', 'low_stock', 'out_of_stock'));

-- Add index for stock status
CREATE INDEX IF NOT EXISTS idx_stock_products_status ON stock_products(status);

-- Add comments
COMMENT ON TABLE pos_orders IS 'Point of Sale orders with payment and receipt tracking';
COMMENT ON TABLE pos_order_lines IS 'Line items for POS orders';
COMMENT ON TABLE payment_settings IS 'Payment gateway settings per organization';
COMMENT ON TABLE stock_transactions IS 'Stock movement history for audit trail';

-- Display success message
DO $$
BEGIN
    RAISE NOTICE '✅ Phase 5 migration completed successfully!';
    RAISE NOTICE '📋 Tables updated: pos_orders, pos_order_lines, payment_settings, finance_incomes';
    RAISE NOTICE '📋 New table created (if not exists): stock_transactions';
    RAISE NOTICE '🔧 Triggers created: generate_receipt_number';
END $$;
