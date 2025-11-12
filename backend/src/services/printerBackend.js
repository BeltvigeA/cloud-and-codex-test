import fetch from 'node-fetch';
import { query } from '../db/connection.js';

const PRINTER_BACKEND_URL = process.env.PRINTER_BACKEND_URL ||
  'https://printer-backend-934564650450.europe-west1.run.app';

/**
 * Get signed URL for GCODE file
 * This is a placeholder - implement based on your cloud storage setup
 * @param {string} filePath - Path to file in cloud storage
 * @returns {Promise<string>} Signed URL
 */
async function getSignedUrl(filePath) {
  // TODO: Implement actual signed URL generation with Google Cloud Storage
  // For now, return the file path as-is
  // In production, this should generate a signed URL with 1-hour expiry
  console.warn('⚠️ Using file path as-is. Implement signed URL generation for production.');
  return filePath;
}

/**
 * Send print job to printer backend
 * @param {string} jobId - Print job UUID
 * @param {string} printerId - Printer UUID
 * @param {string} token - User JWT token
 * @returns {Promise} Response from printer backend
 */
export async function sendPrintJobToPrinterBackend(jobId, printerId, token) {
  try {
    console.log(`📤 Sending job ${jobId} to printer ${printerId}`);

    // 1. Fetch print job
    const jobResult = await query(
      `SELECT * FROM print_jobs WHERE id = $1`,
      [jobId]
    );

    if (jobResult.rows.length === 0) {
      throw new Error('Print job not found');
    }

    const job = jobResult.rows[0];

    // 2. Fetch product (assuming products table exists)
    let product;
    try {
      const productResult = await query(
        `SELECT * FROM products WHERE id = $1`,
        [job.product_id]
      );

      if (productResult.rows.length === 0) {
        throw new Error('Product not found');
      }

      product = productResult.rows[0];
    } catch (error) {
      console.error('Error fetching product:', error);
      throw new Error('Product not found or products table does not exist');
    }

    // 3. Get GCODE file URL
    if (!product.gcode_file_path) {
      throw new Error('Product has no GCODE file');
    }

    const gcodeUrl = await getSignedUrl(product.gcode_file_path);

    // 4. Fetch printer
    const printerResult = await query(
      `SELECT * FROM printers WHERE id = $1`,
      [printerId]
    );

    if (printerResult.rows.length === 0) {
      throw new Error('Printer not found');
    }

    const printer = printerResult.rows[0];

    // 5. Fetch user settings for recipient ID
    let recipientId = 'default-recipient';
    try {
      const settingsResult = await query(
        `SELECT default_recipient_id FROM user_settings
         WHERE organization_id = $1 LIMIT 1`,
        [job.organization_id]
      );

      if (settingsResult.rows.length > 0 && settingsResult.rows[0].default_recipient_id) {
        recipientId = settingsResult.rows[0].default_recipient_id;
      }
    } catch (error) {
      console.warn('Could not fetch user settings, using default recipient ID');
    }

    // 6. Construct complete payload
    const payload = {
      job_id: job.id,
      product_id: product.id,
      product_name: product.name,
      printer_target: {
        printer_id: printer.id,
        printer_name: printer.name,
        printer_model: printer.model,
        ip_address: printer.ip_address,
        access_code: printer.access_code
      },
      gcode_file: {
        url: gcodeUrl,
        file_name: product.gcode_file_path ? product.gcode_file_path.split('/').pop() : 'model.gcode',
        size_bytes: product.gcode_file_size || 0
      },
      print_parameters: {
        plates_requested: job.plates_requested,
        priority: job.priority,
        gcode_details: {
          print_time_seconds: product.estimated_print_time_sec || 0,
          filament_usage_grams: product.estimated_filament_weight || 0,
          nozzle_size_mm: product.gcode_metadata?.extractedData?.nozzleDiameter || 0.4,
          layer_height_mm: product.layer_height || 0.2
        },
        ams_configuration: job.ams_configuration || {
          enabled: false,
          slots: []
        }
      },
      recipient_id: recipientId
    };

    console.log('📦 Payload constructed:', JSON.stringify(payload, null, 2));

    // 7. Send to printer backend
    const response = await fetch(`${PRINTER_BACKEND_URL}/sendPrintJob`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({
        jobId: job.id,
        printerId: printer.id
      })
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Printer backend error: ${response.status} - ${errorText}`);
    }

    const result = await response.json();

    // 8. Update job status
    await query(
      `UPDATE print_jobs SET
        status = 'queued',
        queued_at = NOW(),
        updated_at = NOW()
       WHERE id = $1`,
      [jobId]
    );

    console.log('✅ Job sent successfully');

    return {
      success: true,
      jobId: job.id,
      printerId: printer.id,
      backendResponse: result
    };
  } catch (error) {
    console.error('❌ Error sending job to printer backend:', error);

    // Update job status to failed
    try {
      await query(
        `UPDATE print_jobs SET
          status = 'failed',
          failure_reason = $1,
          updated_at = NOW()
         WHERE id = $2`,
        [error.message, jobId]
      );
    } catch (updateError) {
      console.error('Failed to update job status:', updateError);
    }

    throw error;
  }
}

/**
 * Check printer backend health
 * @returns {Promise<boolean>}
 */
export async function checkPrinterBackendHealth() {
  try {
    const response = await fetch(`${PRINTER_BACKEND_URL}/health`, {
      timeout: 5000
    });
    return response.ok;
  } catch (error) {
    console.error('Printer backend health check failed:', error);
    return false;
  }
}

export default {
  sendPrintJobToPrinterBackend,
  checkPrinterBackendHealth
};
