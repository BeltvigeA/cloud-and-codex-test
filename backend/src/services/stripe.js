import Stripe from 'stripe';
import { query } from '../db/connection.js';

/**
 * Get Stripe client for organization
 * @param {string} organizationId - Organization UUID
 * @returns {Promise} Stripe client
 */
async function getStripeClient(organizationId) {
  const result = await query(
    `SELECT stripe_secret_key FROM payment_settings WHERE organization_id = $1`,
    [organizationId]
  );

  if (result.rows.length === 0 || !result.rows[0].stripe_secret_key) {
    throw new Error('Stripe not configured for this organization');
  }

  return new Stripe(result.rows[0].stripe_secret_key);
}

/**
 * Create payment intent for POS order
 * @param {string} organizationId - Organization UUID
 * @param {number} amount - Amount in smallest currency unit (øre/cent)
 * @param {string} currency - Currency code
 * @param {object} metadata - Optional metadata
 * @returns {Promise} Payment intent
 */
export async function createPaymentIntent(organizationId, amount, currency, metadata = {}) {
  try {
    const stripe = await getStripeClient(organizationId);

    const paymentIntent = await stripe.paymentIntents.create({
      amount: Math.round(amount), // Ensure integer
      currency: currency.toLowerCase(),
      metadata: {
        organizationId,
        ...metadata
      },
      automatic_payment_methods: {
        enabled: true,
      },
    });

    return {
      clientSecret: paymentIntent.client_secret,
      paymentIntentId: paymentIntent.id,
      amount: paymentIntent.amount,
      currency: paymentIntent.currency,
      status: paymentIntent.status
    };
  } catch (error) {
    console.error('Stripe payment intent error:', error);
    throw new Error(`Failed to create payment intent: ${error.message}`);
  }
}

/**
 * Confirm payment intent
 * @param {string} organizationId - Organization UUID
 * @param {string} paymentIntentId - Payment intent ID
 * @returns {Promise} Confirmed payment intent
 */
export async function confirmPaymentIntent(organizationId, paymentIntentId) {
  try {
    const stripe = await getStripeClient(organizationId);

    const paymentIntent = await stripe.paymentIntents.confirm(paymentIntentId);

    return {
      id: paymentIntent.id,
      status: paymentIntent.status,
      amount: paymentIntent.amount,
      currency: paymentIntent.currency
    };
  } catch (error) {
    console.error('Stripe payment confirmation error:', error);
    throw new Error(`Failed to confirm payment: ${error.message}`);
  }
}

/**
 * Retrieve payment intent
 * @param {string} organizationId - Organization UUID
 * @param {string} paymentIntentId - Payment intent ID
 * @returns {Promise} Payment intent
 */
export async function getPaymentIntent(organizationId, paymentIntentId) {
  try {
    const stripe = await getStripeClient(organizationId);

    const paymentIntent = await stripe.paymentIntents.retrieve(paymentIntentId);

    return {
      id: paymentIntent.id,
      status: paymentIntent.status,
      amount: paymentIntent.amount,
      currency: paymentIntent.currency,
      metadata: paymentIntent.metadata
    };
  } catch (error) {
    console.error('Stripe retrieve payment error:', error);
    throw error;
  }
}

/**
 * Create refund
 * @param {string} organizationId - Organization UUID
 * @param {string} paymentIntentId - Payment intent ID
 * @param {number} amount - Amount to refund (optional, full refund if not specified)
 * @returns {Promise} Refund
 */
export async function createRefund(organizationId, paymentIntentId, amount = null) {
  try {
    const stripe = await getStripeClient(organizationId);

    const refundData = { payment_intent: paymentIntentId };
    if (amount) {
      refundData.amount = Math.round(amount);
    }

    const refund = await stripe.refunds.create(refundData);

    return {
      id: refund.id,
      status: refund.status,
      amount: refund.amount,
      currency: refund.currency
    };
  } catch (error) {
    console.error('Stripe refund error:', error);
    throw new Error(`Failed to create refund: ${error.message}`);
  }
}

/**
 * Verify Stripe webhook signature
 * @param {string} payload - Raw request body
 * @param {string} signature - Stripe signature header
 * @param {string} webhookSecret - Webhook secret
 * @returns {object} Event object
 */
export function verifyWebhookSignature(payload, signature, webhookSecret) {
  const stripe = new Stripe('dummy'); // Don't need real key for verification
  return stripe.webhooks.constructEvent(payload, signature, webhookSecret);
}
