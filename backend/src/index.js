import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import printerRoutes from './routes/printers.js';
import printJobRoutes from './routes/print-jobs.js';
import printerCommandRoutes from './routes/printer-commands.js';
import printerStatusRoutes from './routes/printer-status.js';
import { checkPrinterBackendHealth } from './services/printerBackend.js';

// Load environment variables
dotenv.config();

const app = express();
const PORT = process.env.PORT || 8080;

// Middleware
app.use(cors());
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true, limit: '50mb' }));

// Request logging middleware
app.use((req, res, next) => {
  const start = Date.now();
  res.on('finish', () => {
    const duration = Date.now() - start;
    console.log(`${req.method} ${req.path} ${res.statusCode} - ${duration}ms`);
  });
  next();
});

// Health check endpoint
app.get('/health', async (req, res) => {
  try {
    // Check if printer backend is healthy
    const printerBackendHealthy = await checkPrinterBackendHealth();

    res.json({
      status: 'healthy',
      timestamp: new Date().toISOString(),
      service: 'PrintPro3D Backend',
      version: '1.0.0',
      printerBackend: {
        healthy: printerBackendHealthy,
        url: process.env.PRINTER_BACKEND_URL
      }
    });
  } catch (error) {
    res.status(500).json({
      status: 'unhealthy',
      error: error.message
    });
  }
});

// API Routes
app.use('/api/printers', printerRoutes);
app.use('/api/print-jobs', printJobRoutes);
app.use('/api/printer-commands', printerCommandRoutes);
app.use('/api/printer-status', printerStatusRoutes);

// Root endpoint
app.get('/', (req, res) => {
  res.json({
    service: 'PrintPro3D Backend API',
    version: '1.0.0',
    phase: 'Phase 4: Printer Management & Print Queue',
    endpoints: {
      health: '/health',
      printers: '/api/printers',
      printJobs: '/api/print-jobs',
      printerCommands: '/api/printer-commands',
      printerStatus: '/api/printer-status'
    },
    documentation: 'See README.md for API documentation'
  });
});

// 404 handler
app.use((req, res) => {
  res.status(404).json({
    error: 'Not found',
    path: req.path,
    method: req.method
  });
});

// Error handling middleware
app.use((err, req, res, next) => {
  console.error('Error:', err);

  res.status(err.status || 500).json({
    error: err.message || 'Internal server error',
    ...(process.env.NODE_ENV === 'development' && { stack: err.stack })
  });
});

// Start server
app.listen(PORT, '0.0.0.0', () => {
  console.log('');
  console.log('╔════════════════════════════════════════════════════════════════╗');
  console.log('║                    PrintPro3D Backend API                      ║');
  console.log('║              Phase 4: Printer Management & Print Queue        ║');
  console.log('╚════════════════════════════════════════════════════════════════╝');
  console.log('');
  console.log(`🚀 Server running on port ${PORT}`);
  console.log(`📍 Local: http://localhost:${PORT}`);
  console.log(`🔗 Printer Backend: ${process.env.PRINTER_BACKEND_URL || 'Not configured'}`);
  console.log('');
  console.log('📚 Available endpoints:');
  console.log('   GET  /health');
  console.log('   GET  /api/printers');
  console.log('   POST /api/printers');
  console.log('   GET  /api/print-jobs');
  console.log('   POST /api/print-jobs');
  console.log('   POST /api/print-jobs/:id/send');
  console.log('   GET  /api/printer-commands');
  console.log('   POST /api/printer-commands');
  console.log('   GET  /api/printer-status');
  console.log('   POST /api/printer-status');
  console.log('');
  console.log('✅ Ready to accept requests');
  console.log('');
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('SIGTERM signal received: closing HTTP server');
  server.close(() => {
    console.log('HTTP server closed');
    process.exit(0);
  });
});

process.on('SIGINT', () => {
  console.log('SIGINT signal received: closing HTTP server');
  process.exit(0);
});

export default app;
