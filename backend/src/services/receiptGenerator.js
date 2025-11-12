import PDFDocument from 'pdfkit';
import { query } from '../db/connection.js';

/**
 * Generate PDF receipt for POS order
 * @param {string} orderId - Order UUID
 * @param {string} organizationId - Organization UUID
 * @returns {Promise} Receipt generation result
 */
export async function generateReceipt(orderId, organizationId) {
  try {
    // Fetch order with lines
    const orderResult = await query(
      `SELECT
        po.*,
        o.name as org_name,
        ps.company_logo_url,
        ps.receipt_footer_text,
        ps.currency
       FROM pos_orders po
       JOIN organizations o ON po.organization_id = o.id
       LEFT JOIN payment_settings ps ON po.organization_id = ps.organization_id
       WHERE po.id = $1 AND po.organization_id = $2`,
      [orderId, organizationId]
    );

    if (orderResult.rows.length === 0) {
      throw new Error('Order not found');
    }

    const order = orderResult.rows[0];

    // Fetch order lines
    const linesResult = await query(
      `SELECT * FROM pos_order_lines WHERE order_id = $1 ORDER BY created_at`,
      [orderId]
    );

    const lines = linesResult.rows;

    // Create PDF
    const doc = new PDFDocument({ margin: 50 });
    const chunks = [];

    doc.on('data', chunk => chunks.push(chunk));

    // Company header
    doc.fontSize(20).text(order.org_name, { align: 'center' });
    doc.moveDown(0.5);

    // Receipt title and number
    doc.fontSize(16).text('KVITTERING', { align: 'center' });
    doc.fontSize(10).text(`Kvitteringsnr: ${order.receipt_number}`, { align: 'center' });
    doc.moveDown(1);

    // Order info
    doc.fontSize(10);
    doc.text(`Dato: ${new Date(order.sale_timestamp || order.created_at).toLocaleString('no-NO')}`);
    doc.text(`Betalingsmetode: ${order.payment_method}`);
    if (order.customer_name) {
      doc.text(`Kunde: ${order.customer_name}`);
    }
    doc.moveDown(1);

    // Table header
    doc.text('─'.repeat(80));
    doc.text('Produkt                       Ant.    Pris      Total');
    doc.text('─'.repeat(80));

    // Order lines
    lines.forEach(line => {
      const productName = line.product_name.substring(0, 25).padEnd(25);
      const qty = line.quantity.toString().padStart(5);
      const price = `${parseFloat(line.unit_price).toFixed(2)} ${order.currency || 'NOK'}`.padStart(10);
      const total = `${parseFloat(line.line_total).toFixed(2)} ${order.currency || 'NOK'}`.padStart(10);

      doc.text(`${productName} ${qty}  ${price}  ${total}`);
    });

    doc.text('─'.repeat(80));
    doc.moveDown(0.5);

    // Totals
    if (order.discount_amount > 0) {
      doc.text(`Subtotal: ${parseFloat(order.subtotal).toFixed(2)} ${order.currency || 'NOK'}`, { align: 'right' });
      doc.text(`Rabatt: -${parseFloat(order.discount_amount).toFixed(2)} ${order.currency || 'NOK'}`, { align: 'right' });
    }
    if (order.tax_amount > 0) {
      doc.text(`MVA (${order.tax_percentage}%): ${parseFloat(order.tax_amount).toFixed(2)} ${order.currency || 'NOK'}`, { align: 'right' });
    }
    doc.fontSize(12).text(`TOTALT: ${parseFloat(order.total_amount).toFixed(2)} ${order.currency || 'NOK'}`, { align: 'right', underline: true });

    // Payment info
    doc.fontSize(10).moveDown(1);
    if (order.payment_method === 'Cash' && order.amount_paid) {
      doc.text(`Betalt: ${parseFloat(order.amount_paid).toFixed(2)} ${order.currency || 'NOK'}`, { align: 'right' });
      if (order.change_given > 0) {
        doc.text(`Veksel: ${parseFloat(order.change_given).toFixed(2)} ${order.currency || 'NOK'}`, { align: 'right' });
      }
    }

    // Footer
    doc.moveDown(2);
    if (order.receipt_footer_text) {
      doc.fontSize(8).text(order.receipt_footer_text, { align: 'center' });
    }
    doc.text('Takk for handelen!', { align: 'center' });

    // Finalize PDF
    doc.end();

    // Wait for PDF generation to complete
    const pdfBuffer = await new Promise((resolve, reject) => {
      doc.on('end', () => resolve(Buffer.concat(chunks)));
      doc.on('error', reject);
    });

    // For now, store the path as a placeholder
    // In production, this would upload to Cloud Storage
    const filename = `receipt-${order.receipt_number}.pdf`;
    const storagePath = `${organizationId}/pos_order/${orderId}/${filename}`;

    // Update order with receipt path
    await query(
      `UPDATE pos_orders SET receipt_pdf_path = $1, updated_at = NOW() WHERE id = $2`,
      [storagePath, orderId]
    );

    console.log(`✅ Receipt generated: ${filename}`);
    console.log(`⚠️ PDF storage not implemented. In production, upload to Cloud Storage.`);

    return {
      success: true,
      receiptPath: storagePath,
      receiptNumber: order.receipt_number,
      filename: filename,
      pdfBuffer: pdfBuffer // Return buffer for testing
    };
  } catch (error) {
    console.error('Receipt generation error:', error);
    throw error;
  }
}
