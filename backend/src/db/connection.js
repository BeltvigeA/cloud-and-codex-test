import pg from 'pg';
import dotenv from 'dotenv';

dotenv.config();

const { Pool } = pg;

// Database connection configuration
const poolConfig = {
  host: process.env.DB_HOST || 'localhost',
  port: parseInt(process.env.DB_PORT || '5432'),
  database: process.env.DB_NAME || 'printpro3d',
  user: process.env.DB_USER || 'postgres',
  password: process.env.DB_PASSWORD,
  max: 20, // Maximum number of clients in the pool
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 2000,
};

// Create connection pool
const pool = new Pool(poolConfig);

// Handle pool errors
pool.on('error', (err) => {
  console.error('❌ Unexpected error on idle client', err);
  process.exit(-1);
});

// Test connection on startup
pool.query('SELECT NOW()', (err, res) => {
  if (err) {
    console.error('❌ Database connection failed:', err);
  } else {
    console.log('✅ Database connected successfully at:', res.rows[0].now);
  }
});

/**
 * Execute a SQL query
 * @param {string} text - SQL query string
 * @param {Array} params - Query parameters
 * @returns {Promise} Query result
 */
export async function query(text, params) {
  const start = Date.now();
  try {
    const res = await pool.query(text, params);
    const duration = Date.now() - start;

    if (duration > 1000) {
      console.warn(`⚠️ Slow query (${duration}ms):`, text.substring(0, 100));
    }

    return res;
  } catch (error) {
    console.error('❌ Database query error:', error.message);
    console.error('Query:', text);
    console.error('Params:', params);
    throw error;
  }
}

/**
 * Get a client from the pool for transactions
 * @returns {Promise} Database client
 */
export async function getClient() {
  return await pool.connect();
}

/**
 * Close the database pool
 */
export async function closePool() {
  await pool.end();
  console.log('Database pool closed');
}

export default {
  query,
  getClient,
  closePool,
  pool
};
