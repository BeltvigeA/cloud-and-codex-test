# PrintPro3D Backend API

**Phase 4: Printer Management & Print Queue System**

A Node.js/Express backend for managing 3D printers, print jobs, printer commands, and real-time status tracking.

## 🎯 Overview

This backend serves as the central management system for PrintPro3D, handling:

- **Printer Management**: Register and configure 3D printers
- **Print Queue**: Job creation, prioritization, and tracking
- **Command System**: Queue commands for printer-agents to execute
- **Status Tracking**: Real-time printer status monitoring
- **Backend Integration**: Sends complete print jobs to external printer backend API

## 📊 Architecture

```
┌─────────────┐     HTTPS      ┌────────────────────┐     PostgreSQL    ┌──────────────┐
│   Clients   │ ──────────────► │ PrintPro3D Backend │ ────────────────► │   Database   │
│  (Web/App)  │                 │   (This API)       │                   │  (Products,  │
└─────────────┘                 └────────────────────┘                   │   Printers,  │
                                         │                                │    Jobs)     │
                                         │                                └──────────────┘
                                         │ HTTPS
                                         ▼
                                ┌─────────────────────┐
                                │  Printer Backend    │
                                │  (Python/Flask)     │
                                └─────────────────────┘
                                         │ MQTT/LAN
                                         ▼
                                ┌─────────────────────┐
                                │  Physical Printer   │
                                │  (Bambu Lab, etc.)  │
                                └─────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- Node.js 18+
- PostgreSQL 14+
- npm or yarn

### Installation

```bash
# Navigate to backend directory
cd backend

# Install dependencies
npm install

# Copy environment file
cp .env.example .env

# Edit .env with your configuration
nano .env

# Run database migrations
psql -U postgres -d printpro3d -f src/db/schema.sql
```

### Development

```bash
# Start development server with auto-reload
npm run dev

# Or start production server
npm start
```

The server will start on `http://localhost:8080`

## 📚 API Endpoints

### Health Check

```
GET /health
```

Returns API health status and printer backend connectivity.

### Printers

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/printers` | Create new printer |
| GET | `/api/printers` | List all printers |
| GET | `/api/printers/:id` | Get single printer with status |
| PUT | `/api/printers/:id` | Update printer configuration |
| DELETE | `/api/printers/:id` | Delete printer (soft delete) |

**Query Parameters:**
- `organizationId` (required) - Organization UUID
- `status` (optional) - Filter by status (idle, printing, offline, etc.)

**Example: Create Printer**
```json
POST /api/printers
Authorization: Bearer <token>

{
  "organizationId": "uuid",
  "name": "Bambu Lab X1C #1",
  "brand": "Bambu Lab",
  "model": "X1 Carbon",
  "ip_address": "192.168.1.100",
  "access_code": "12345678",
  "num_ams_units": 1,
  "supported_materials": ["PLA", "PETG", "ABS"]
}
```

### Print Jobs

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/print-jobs` | Create new print job |
| POST | `/api/print-jobs/:id/send` | Send job to printer backend |
| GET | `/api/print-jobs` | List all print jobs |
| GET | `/api/print-jobs/:id` | Get single print job |
| PUT | `/api/print-jobs/:id/status` | Update job status/progress |
| POST | `/api/print-jobs/:id/cancel` | Cancel print job |
| DELETE | `/api/print-jobs/:id` | Delete print job |

**Example: Create and Send Job**
```json
POST /api/print-jobs
{
  "organizationId": "uuid",
  "product_id": "uuid",
  "printer_id": "uuid",
  "plates_requested": 1,
  "priority": "normal",
  "ams_configuration": {
    "enabled": true,
    "slots": [
      {
        "slot_number": 1,
        "filament_type": "PLA",
        "filament_color": "Red"
      }
    ]
  }
}

POST /api/print-jobs/{job_id}/send
{
  "organizationId": "uuid",
  "printerId": "uuid"
}
```

### Printer Commands

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/printer-commands` | Create printer command |
| GET | `/api/printer-commands` | List all commands |
| GET | `/api/printer-commands/pending` | Get pending commands (for agent) |
| PUT | `/api/printer-commands/:id/status` | Update command status |
| DELETE | `/api/printer-commands/:id` | Delete command |

**Command Types:**
- `start_print`, `pause_print`, `resume_print`, `stop_print`, `cancel_print`
- `set_bed_temp`, `set_nozzle_temp`, `set_chamber_temp`
- `home_all`, `home_x`, `home_y`, `home_z`
- `set_fan_speed`, `set_print_speed`, `set_flow_rate`
- `camera_on`, `camera_off`, `light_on`, `light_off`
- `load_filament`, `unload_filament`
- `custom_gcode`

**Example: Set Bed Temperature**
```json
POST /api/printer-commands
{
  "organizationId": "uuid",
  "printer_id": "uuid",
  "command_type": "set_bed_temp",
  "metadata": {
    "target": 60
  }
}
```

### Printer Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/printer-status` | Create status update |
| GET | `/api/printer-status/latest/:printerId` | Get latest status |
| GET | `/api/printer-status/history/:printerId` | Get status history |

**Example: Update Status**
```json
POST /api/printer-status
{
  "printer_id": "uuid",
  "organizationId": "uuid",
  "status": "printing",
  "current_job_id": "uuid",
  "progress_percentage": 45.5,
  "current_layer": 120,
  "total_layers": 250,
  "nozzle_temp_current": 218.5,
  "bed_temp_current": 59.8,
  "time_remaining_seconds": 4200
}
```

## 🗄️ Database Schema

The backend uses PostgreSQL with the following main tables:

- **printers**: Printer registry with configuration
- **print_jobs**: Print job queue and history
- **printer_status**: Real-time status updates (keep last 1000 per printer)
- **printer_commands**: Command queue for printer-agents

See `src/db/schema.sql` for complete schema.

## 🔐 Authentication

All endpoints require JWT authentication via the `Authorization` header:

```
Authorization: Bearer <your-jwt-token>
```

The JWT should contain:
- `userId` or `sub` or `id` - User UUID
- `email` - User email
- `organizationId` - Organization UUID (optional)

## 🌐 Printer Backend Integration

This backend integrates with an external printer backend API:

**URL:** `https://printer-backend-934564650450.europe-west1.run.app`

**Flow:**
1. Client creates print job via PrintPro3D Backend
2. PrintPro3D Backend sends complete payload to Printer Backend
3. Printer Backend communicates with physical printers via LAN agents
4. Status updates flow back through both systems

**Payload sent to printer backend:**
```json
{
  "job_id": "uuid",
  "product_id": "uuid",
  "product_name": "Product Name",
  "printer_target": {
    "printer_id": "uuid",
    "printer_name": "Bambu X1C",
    "ip_address": "192.168.1.100",
    "access_code": "12345678"
  },
  "gcode_file": {
    "url": "https://signed-url...",
    "file_name": "model.gcode"
  },
  "print_parameters": { ... }
}
```

## 🛠️ Development

### Project Structure

```
backend/
├── src/
│   ├── db/
│   │   ├── connection.js      # Database connection pool
│   │   └── schema.sql         # Database schema
│   ├── middleware/
│   │   └── auth.js            # JWT authentication
│   ├── routes/
│   │   ├── printers.js        # Printer CRUD endpoints
│   │   ├── print-jobs.js      # Print job management
│   │   ├── printer-commands.js # Command queue
│   │   └── printer-status.js  # Status tracking
│   ├── services/
│   │   └── printerBackend.js  # Printer backend integration
│   └── index.js               # Main application
├── .env.example               # Environment template
├── .gitignore
├── package.json
└── README.md
```

### Environment Variables

See `.env.example` for all available configuration options.

Key variables:
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` - PostgreSQL connection
- `JWT_SECRET` - Secret for JWT verification
- `PRINTER_BACKEND_URL` - External printer backend API URL
- `PORT` - Server port (default: 8080)

## 📊 Monitoring

### Database Queries

```sql
-- Active print jobs
SELECT COUNT(*), status FROM print_jobs
WHERE status IN ('pending', 'queued', 'printing')
GROUP BY status;

-- Printer utilization
SELECT p.name, p.current_status, COUNT(pj.id) as total_jobs
FROM printers p
LEFT JOIN print_jobs pj ON p.id = pj.printer_id
GROUP BY p.id, p.name, p.current_status;

-- Pending commands
SELECT COUNT(*) FROM printer_commands WHERE status = 'pending';
```

### Health Check

```bash
curl http://localhost:8080/health
```

Returns:
```json
{
  "status": "healthy",
  "timestamp": "2025-11-12T10:30:00.000Z",
  "service": "PrintPro3D Backend",
  "printerBackend": {
    "healthy": true,
    "url": "https://printer-backend-934564650450.europe-west1.run.app"
  }
}
```

## 🚨 Error Handling

All errors return consistent JSON format:

```json
{
  "error": "Error message description"
}
```

HTTP Status Codes:
- `200` - Success
- `201` - Created
- `400` - Bad Request (validation error)
- `401` - Unauthorized (missing/invalid token)
- `403` - Forbidden (insufficient permissions)
- `404` - Not Found
- `500` - Internal Server Error

## 🔄 Database Maintenance

### Cleanup Old Status Records

Printer status table can grow large. Run cleanup regularly:

```sql
-- Keep only last 1000 status records per printer
SELECT cleanup_old_printer_status();
```

Or set up a cron job:
```sql
-- Run daily at 2 AM
CREATE EXTENSION IF NOT EXISTS pg_cron;
SELECT cron.schedule('cleanup-printer-status', '0 2 * * *', 'SELECT cleanup_old_printer_status()');
```

## 📦 Deployment

### Local Deployment

```bash
npm install
npm start
```

### Docker Deployment

```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY src ./src
EXPOSE 8080
CMD ["node", "src/index.js"]
```

### Google Cloud Run

```bash
gcloud run deploy printpro3d-backend \
  --source . \
  --region europe-west1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars PRINTER_BACKEND_URL=https://printer-backend-934564650450.europe-west1.run.app
```

## 🧪 Testing

### Manual Testing with curl

```bash
# Health check
curl http://localhost:8080/health

# Create printer (requires JWT)
curl -X POST http://localhost:8080/api/printers \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "organizationId": "uuid",
    "name": "Test Printer",
    "ip_address": "192.168.1.100"
  }'

# List printers
curl "http://localhost:8080/api/printers?organizationId=uuid" \
  -H "Authorization: Bearer <token>"
```

### Postman Collection

Import the Postman collection from the main project documentation for complete API testing.

## 📝 License

Proprietary - All rights reserved

## 🤝 Contributing

This is a private project. Contact the project maintainer for contribution guidelines.

---

**Phase 4 Implementation Status:** ✅ Complete

Implemented:
- ✅ Printer registration and management
- ✅ Print job queue system
- ✅ Printer command queue
- ✅ Real-time status tracking
- ✅ Printer backend integration
- ✅ Complete REST API with authentication

Next phases:
- Phase 5: Maintenance Management
- Phase 6: Advanced Analytics & Reporting
