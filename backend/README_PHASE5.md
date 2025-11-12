# PrintPro3D Backend API - Phase 5

**Point of Sale (POS) System med Betalingsintegrasjon**

En komplett Node.js/Express backend for håndtering av 3D-printere, printjobber, lager og et fullstendig Point of Sale-system med betalingsprosessering.

## 🎯 Oversikt

Dette backend-systemet er det sentrale styringssystemet for PrintPro3D og håndterer:

### Core Funksjoner
- **Printer Management**: Registrering og konfigurering av 3D-printere
- **Print Queue**: Jobbopprettelse, prioritering og sporing
- **Command System**: Kommandokø for printer-agenter
- **Status Tracking**: Sanntids printerstatusovervåkning

### 🆕 Fase 5 Funksjoner
- **Point of Sale**: Komplett POS-system med multi-produkt ordre
- **Inventory Management**: Automatisk lagersporing og oppdateringer
- **Payment Processing**: Stripe, Vipps, MobilePay og kontant
- **Receipt Generation**: Automatisk PDF-kvitteringsgenerering
- **Finance Integration**: Inntektssporing og regnskap
- **Stock Transactions**: Fullstendig revisjonsspor for lagerbevegelser

## 📊 Arkitektur

```
┌─────────────┐     HTTPS      ┌────────────────────┐     PostgreSQL    ┌──────────────┐
│   Klienter  │ ──────────────► │ PrintPro3D Backend │ ────────────────► │   Database   │
│  (Web/App)  │                 │   (Denne API)      │                   │  (Produkter, │
└─────────────┘                 └────────────────────┘                   │   Printere,  │
                                         │                                │   Ordre, POS)│
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
                                │  Fysisk Printer     │
                                │  (Bambu Lab, etc.)  │
                                └─────────────────────┘
```

## 🚀 Kom i Gang

### Forutsetninger

- Node.js 18+
- PostgreSQL 15+
- npm eller yarn

### Installasjon

```bash
# Naviger til backend-katalog
cd backend

# Installer dependencies
npm install

# Kopier environment fil
cp .env.example .env

# Rediger .env med din konfigurasjon
nano .env

# Kjør database migrasjoner
psql -U postgres -d printpro3d -f src/db/schema.sql

# Kjør Fase 5 migrasjon
psql -U postgres -d printpro3d -f src/db/migrate_phase5.sql
```

### Utvikling

```bash
# Start utviklingsserver med auto-reload
npm run dev

# Eller start produksjonsserver
npm start
```

Serveren starter på `http://localhost:8080`

## 💰 POS System - Phase 5

### Betalingsmetoder

| Metode | Status | Beskrivelse |
|--------|--------|-------------|
| Cash | ✅ Fullført | Kontant med vekselberegning |
| Stripe | ✅ Fullført | Payment Intents API |
| Vipps | 🔄 Placeholder | Klar for implementasjon |
| MobilePay | 🔄 Placeholder | Klar for implementasjon |

### POS Arbeidsflyt

#### Kontant Betaling
```
1. Opprett ordre → POST /api/pos-orders
2. Prosesser betaling → POST /api/pos-orders/:id/pay
   - payment_method: "Cash"
   - amount_paid: 500.00
3. System beregner veksel automatisk
4. Lager trekkes automatisk
5. Finance income opprettets
6. PDF-kvittering genereres
7. Status: "Paid"
```

#### Stripe Betaling
```
1. Opprett ordre → POST /api/pos-orders
2. Initier betaling → POST /api/pos-orders/:id/pay
   - payment_method: "Stripe"
3. Motta Payment Intent ID
4. Klient fullfører betaling (Stripe Elements)
5. Webhook oppdaterer status
6. Ved suksess: Trekk lager, opprett income, generer kvittering
```

### Lageroppdatering

System håndterer automatisk:
- ✅ Lagertrekk ved fullført betaling
- ✅ Lager-gjenoppretting ved kansellering
- ✅ Lager-gjenoppretting ved refundering
- ✅ Status oppdatering (in_stock, low_stock, out_of_stock)
- ✅ Transaksjonsspor for revisjon

## 📚 API Endpoints

### Health Check

```
GET /health
```

### POS Ordre (NYE i Fase 5)

| Metode | Endpoint | Beskrivelse |
|--------|----------|-------------|
| POST | `/api/pos-orders` | Opprett ny ordre |
| POST | `/api/pos-orders/:id/pay` | Prosesser betaling |
| GET | `/api/pos-orders` | Liste alle ordre |
| GET | `/api/pos-orders/:id` | Hent enkelt ordre med linjer |
| POST | `/api/pos-orders/:id/cancel` | Kanseller ordre |
| POST | `/api/pos-orders/:id/refund` | Refunder ordre (admin) |
| GET | `/api/pos-orders/:id/receipt` | Hent kvittering |

**Eksempel: Opprett Ordre**
```json
POST /api/pos-orders
Authorization: Bearer <token>

{
  "organizationId": "uuid",
  "customer_name": "John Doe",
  "customer_email": "john@example.com",
  "note": "Kunde ønsker ekstra emballasje",
  "lines": [
    {
      "product_id": "uuid",
      "quantity": 2,
      "discount_amount": 0,
      "allow_backorder": false
    },
    {
      "product_id": "uuid2",
      "quantity": 1,
      "unit_price": 299.00
    }
  ]
}
```

**Eksempel: Prosesser Kontant Betaling**
```json
POST /api/pos-orders/{order_id}/pay
Authorization: Bearer <token>

{
  "organizationId": "uuid",
  "payment_method": "Cash",
  "amount_paid": 500.00
}

Response:
{
  "order": {
    "id": "uuid",
    "status": "Paid",
    "payment_status": "completed",
    "total_amount": 450.00,
    "change_given": 50.00,
    "receipt_number": "REC-20241112-000001"
  },
  "changeGiven": 50.00
}
```

### Betalingsinnstillinger (NYE)

| Metode | Endpoint | Beskrivelse |
|--------|----------|-------------|
| GET | `/api/payment-settings` | Hent innstillinger |
| PUT | `/api/payment-settings` | Oppdater innstillinger (admin) |

**Eksempel: Konfigurer Stripe**
```json
PUT /api/payment-settings
Authorization: Bearer <token>

{
  "organizationId": "uuid",
  "stripe_enabled": true,
  "stripe_publishable_key": "pk_test_...",
  "stripe_secret_key": "sk_test_...",
  "country": "NO",
  "currency": "NOK",
  "default_tax_percentage": 25.00,
  "receipt_footer_text": "Takk for handelen! Returrett 30 dager."
}
```

### Lagertransaksjoner (NYE)

| Metode | Endpoint | Beskrivelse |
|--------|----------|-------------|
| GET | `/api/stock-transactions` | Hent transaksjonsliste |

Query parameters:
- `organizationId` (påkrevd)
- `productId` (valgfri) - Filtrer på produkt
- `transactionType` (valgfri) - sale, return, restock, etc.
- `from_date`, `to_date` (valgfri) - Datointervall
- `limit`, `offset` (valgfri) - Paginering

### Eksisterende Endpoints

**Printere**
- POST `/api/printers` - Opprett printer
- GET `/api/printers` - Liste printere
- GET `/api/printers/:id` - Hent printer med status
- PUT `/api/printers/:id` - Oppdater printer
- DELETE `/api/printers/:id` - Slett printer

**Printjobber**
- POST `/api/print-jobs` - Opprett jobb
- POST `/api/print-jobs/:id/send` - Send til printer backend
- GET `/api/print-jobs` - Liste jobber
- GET `/api/print-jobs/:id` - Hent jobb
- PUT `/api/print-jobs/:id/status` - Oppdater status
- POST `/api/print-jobs/:id/cancel` - Kanseller jobb

**Printer Kommandoer**
- POST `/api/printer-commands` - Opprett kommando
- GET `/api/printer-commands` - Liste kommandoer
- GET `/api/printer-commands/pending` - Ventende kommandoer
- PUT `/api/printer-commands/:id/status` - Oppdater status

**Printer Status**
- POST `/api/printer-status` - Opprett statusoppdatering
- GET `/api/printer-status/latest/:printerId` - Siste status
- GET `/api/printer-status/history/:printerId` - Statushistorikk

## 🗄️ Database Schema (Fase 5)

### Nye Tabeller

#### pos_orders (30 kolonner)
- Kundeinformasjon (navn, email, telefon)
- Økonomiske felt (subtotal, mva, total, rabatter)
- Betalingssporing (status, intent_id, beløp betalt, veksel)
- Kvitteringshåndtering (nummer, PDF-sti)
- Tidsstempler (betalt, refundert)

#### pos_order_lines
- Produkt-snapshots (navn, SKU)
- Prising (enhetspris, rabatt, linjetotal)
- Lagerhåndtering (stock_deducted, allow_backorder)

#### payment_settings
- Stripe konfigurasjon
- Vipps/MobilePay innstillinger
- Mva og valuta
- Kvitteringstilpasning

#### stock_transactions
- Fullstendig revisjonsspor
- Transaksjontyper (sale, return, restock, etc.)
- Referanser (pos_order, print_job)

### Oppdaterte Tabeller

- **finance_incomes** - Lagt til `pos_order_id` kolonne
- **products** - Lagt til `stock_tracked` kolonne
- **stock_products** - Lagt til `status` og `last_movement_at`

## 🔐 Autentisering

Alle endpoints krever JWT-autentisering via `Authorization` header:

```
Authorization: Bearer <your-jwt-token>
```

JWT-en skal inneholde:
- `userId` eller `sub` eller `id` - Bruker UUID
- `email` - Bruker email
- `organizationId` - Organisasjon UUID (valgfri)

## 🔧 Services (Fase 5)

### stripe.js
```javascript
import { createPaymentIntent, confirmPaymentIntent, createRefund } from './services/stripe.js';

// Opprett Payment Intent
const intent = await createPaymentIntent(orgId, 50000, 'NOK', { orderId: '...' });

// Hent status
const status = await getPaymentIntent(orgId, intent.paymentIntentId);

// Refunder
await createRefund(orgId, intent.paymentIntentId, 50000);
```

### stockManager.js
```javascript
import { checkStockAvailability, deductStock, restoreStock } from './services/stockManager.js';

// Sjekk tilgjengelighet
const available = await checkStockAvailability(productId, 5);
// { canFulfill: true, currentStock: 10 }

// Trekk lager
await deductStock(orgId, productId, 5, 'pos_order', orderId, userId);

// Gjenopprett lager
await restoreStock(orgId, productId, 5, 'pos_order', orderId, userId, 'Ordre kansellert');
```

### receiptGenerator.js
```javascript
import { generateReceipt } from './services/receiptGenerator.js';

// Generer kvittering
const result = await generateReceipt(orderId, organizationId);
// { success: true, receiptPath: '...', receiptNumber: 'REC-20241112-000001' }
```

## 📦 Dependencies (Fase 5)

Nye dependencies:
```json
{
  "stripe": "^14.10.0",
  "pdfkit": "^0.14.0"
}
```

Installer med:
```bash
npm install stripe pdfkit
```

## 🧪 Testing

### Opprett Testordre

```bash
# Opprett ordre
curl -X POST http://localhost:8080/api/pos-orders \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "organizationId": "uuid",
    "lines": [
      {
        "product_id": "product-uuid",
        "quantity": 2
      }
    ]
  }'

# Prosesser kontant betaling
curl -X POST http://localhost:8080/api/pos-orders/{order_id}/pay \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "organizationId": "uuid",
    "payment_method": "Cash",
    "amount_paid": 500.00
  }'
```

### Verifiser Lager

```sql
-- Sjekk lagertransaksjoner
SELECT * FROM stock_transactions
WHERE reference_type = 'pos_order'
ORDER BY transaction_date DESC
LIMIT 10;

-- Sjekk ordre
SELECT * FROM pos_orders
WHERE status = 'Paid'
ORDER BY created_at DESC
LIMIT 10;
```

## 📊 Monitoring

### POS Statistikk

```sql
-- Daglig salg
SELECT
    DATE(created_at) as date,
    COUNT(*) as orders,
    SUM(total_amount) as revenue
FROM pos_orders
WHERE status = 'Paid'
  AND created_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY DATE(created_at)
ORDER BY date DESC;

-- Betalingsmetoder
SELECT
    payment_method,
    COUNT(*) as count,
    SUM(total_amount) as total_revenue
FROM pos_orders
WHERE status = 'Paid'
GROUP BY payment_method;

-- Lageroppdateringer
SELECT
    transaction_type,
    COUNT(*) as count,
    SUM(ABS(quantity_change)) as total_quantity
FROM stock_transactions
WHERE transaction_date >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY transaction_type;
```

## 🚀 Deployment

### Google Cloud Run

```bash
gcloud run deploy printpro3d-backend \
  --source . \
  --region europe-west1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars DATABASE_URL=postgres://...,JWT_SECRET=...
```

### Environment Variables

Viktige miljøvariabler for Fase 5:
```bash
# Database
DATABASE_URL=postgres://user:pass@host:5432/printpro3d

# Auth
JWT_SECRET=your-secret-key

# Stripe (lagres i payment_settings tabell per organisasjon)
# STRIPE_WEBHOOK_SECRET=whsec_...

# Printer Backend
PRINTER_BACKEND_URL=https://printer-backend-934564650450.europe-west1.run.app
```

## 🎯 Implementasjonsstatus

### ✅ Fase 5 Fullført (November 2024)

- ✅ POS ordre-opprettelse med multi-produkt
- ✅ Kontant betalingsprosessering
- ✅ Stripe Payment Intents integrasjon
- ✅ Automatisk lageroppdatering
- ✅ Lager-gjenoppretting ved kansellering
- ✅ Lager-gjenoppretting ved refundering
- ✅ PDF-kvitteringsgenerering
- ✅ Auto-kvitteringsnummer (REC-YYYYMMDD-000001)
- ✅ Finance income integrasjon
- ✅ Lagertransaksjons-logging
- ✅ Rolle-basert tilgangskontroll
- ✅ Database transaksjons-sikkerhet
- ✅ Komplett API dokumentasjon

### 🔄 Planlagt

- Vipps fullstendig integrasjon
- MobilePay fullstendig integrasjon
- Cloud Storage for PDF-kvitteringer
- Email-kvitteringer
- Kundekort/lojalitetsprogram
- Rabattkoder

## 📝 Lisens

Proprietary - Alle rettigheter reservert

---

**Fase 5 Implementert:** 12. november 2024
**Branch:** `claude/implement-pos-system-phase-5-011CV4dX3exntSq33QT7GWjw`
**Status:** ✅ Produksjonsklar
