# Printer Event Reporting System

## Oversikt

Event reporting systemet sender hendelser fra Python-klienten til backend for å rapportere:
- **Jobbstatus**: Print job startet, fullført, feilet
- **HMS feilkoder**: Hardware Management System errors fra Bambu printere
- **Status oppdateringer**: Periodiske status updates (temperatur, progresjon, etc.)

## Konfigurering

### ⚠️ VIKTIG: Bruk Config-fil, IKKE Environment Variables

**ALDRI hardkode API keys, organisation IDs eller recipient IDs!**

Alle credentials skal lagres i config-filen `~/.printmaster/config.json`.

### 1. Opprett Config-fil

Opprett eller rediger `~/.printmaster/config.json`:

```json
{
  "backend_url": "https://printpro3d-api-931368217793.europe-west1.run.app",
  "api_key": "your-api-key-here",
  "recipient_id": "your-recipient-id-here",
  "organization_id": "your-organization-id-here"
}
```

### 2. Sikre Riktige Tillatelser

Config-filen inneholder sensitive API keys, så sett riktige tillatelser:

```bash
chmod 600 ~/.printmaster/config.json
```

Dette sikrer at kun eieren kan lese/skrive filen.

### 3. Konfigurasjonsfelter

| Felt | Beskrivelse | Eksempel |
|------|-------------|----------|
| `backend_url` | Backend API URL | `https://printpro3d-api-931368217793.europe-west1.run.app` |
| `api_key` | API authentication key | `your-secret-api-key` |
| `recipient_id` | Unique recipient identifier | `recipient-uuid-here` |
| `organization_id` | Organization identifier (optional) | `org-uuid-here` |

### 4. Verifiser Konfigurasjonen

Test at konfigurasjonen er korrekt:

```python
from client.config_manager import get_config_manager

config = get_config_manager()
print(f"Backend URL: {config.get_backend_url()}")
print(f"API Key configured: {bool(config.get_api_key())}")
print(f"Recipient ID: {config.get_recipient_id()}")
print(f"Is configured: {config.is_configured()}")
```

## Hvordan det Fungerer

### 1. Automatisk Initialisering

Event reporter initialiseres automatisk når klienten starter hvis config-filen er korrekt konfigurert:

```python
# I status_subscriber.py
if _config_manager_available:
    config = get_config_manager()
    base_url = config.get_backend_url()
    api_key = config.get_api_key()
    recipient_id = config.get_recipient_id()

event_reporter = EventReporter(
    base_url=base_url,
    api_key=api_key,
    recipient_id=recipient_id
)
```

### 2. Event Types

#### Job Lifecycle Events

```python
# Automatisk rapportert når print job starter
event_reporter.report_job_started(
    printer_serial="01P00A123",
    printer_ip="192.168.1.100",
    print_job_id="job-uuid",
    file_name="model.3mf"
)

# Automatisk rapportert når print job er ferdig
event_reporter.report_job_completed(
    printer_serial="01P00A123",
    printer_ip="192.168.1.100",
    print_job_id="job-uuid",
    file_name="model.3mf"
)

# Automatisk rapportert ved feil
event_reporter.report_job_failed(
    printer_serial="01P00A123",
    printer_ip="192.168.1.100",
    print_job_id="job-uuid",
    file_name="model.3mf",
    error_message="Print failed: heater malfunction"
)
```

#### HMS Error Events

```python
# Automatisk detektert og rapportert fra printer status
event_reporter.report_hms_error(
    printer_serial="01P00A123",
    printer_ip="192.168.1.100",
    hms_code="0300_0300_0002_0003",
    error_data={
        "hmsCode": "0300_0300_0002_0003",
        "description": "Hotbed heating abnormal",
        "severity": "critical",
        "module": "hotbed"
    },
    image_data=b"..."  # Optional camera snapshot
)
```

#### Status Updates

```python
# Automatisk sendt hver 5. minutt
event_reporter.report_event(
    event_type="status_update",
    printer_serial="01P00A123",
    printer_ip="192.168.1.100",
    event_status="info",
    status_data={
        "progress": 45.5,
        "bedTemp": 60,
        "nozzleTemp": 220,
        "remainingTimeSeconds": 3600
    }
)
```

### 3. HMS Error Codes

HMS (Hardware Management System) error codes har formatet `XXXX_YYYY_ZZZZ_WWWW`.

#### Kjente HMS Error Codes

| HMS Code | Modul | Alvorlighet | Beskrivelse |
|----------|-------|-------------|-------------|
| `0300_0300_0002_0003` | hotbed | critical | Hotbed heating abnormal |
| `0500_0200_0001_0001` | extruder | error | Nozzle temperature abnormal |
| `0500_0300_0002_0001` | extruder | critical | Nozzle heating failed |
| `0700_0300_0001_0002` | motion | error | Homing failed |
| `0C00_0100_0001_0001` | ams | error | AMS communication error |
| `0D00_0200_0001_0001` | filament | error | Filament runout |

#### Modul Mapping

| Kode | Modul |
|------|-------|
| `0300` | hotbed |
| `0500` | extruder |
| `0700` | motion |
| `0C00` | ams |
| `0D00` | filament |
| `1200` | chamber |

## Feilsøking

### Event Reporting Ikke Aktivert

Hvis du ser denne loggen:
```
Event reporting not configured (missing from config: backend_url, api_key, recipient_id)
```

**Løsning:**
1. Sjekk at `~/.printmaster/config.json` eksisterer
2. Verifiser at alle felter er satt korrekt
3. Kjør `config.is_configured()` for å teste

### API Key Feil

Hvis du ser denne loggen:
```
Failed to report event: 401 Unauthorized
```

**Løsning:**
1. Verifiser at `api_key` i config-filen er korrekt
2. Sjekk at API key ikke har utløpt
3. Test med ny API key

### Network Feil

Hvis du ser denne loggen:
```
Failed to report event: Connection timeout
```

**Løsning:**
1. Sjekk nettverkstilkobling
2. Verifiser at `backend_url` er korrekt
3. Test med `curl` eller `ping` til backend URL

## Sikkerhet

### ⚠️ ALDRI Commit Config-filen til Git

Legg til i `.gitignore`:
```
.printmaster/
config.json
*.key
*.secret
```

### ⚠️ ALDRI Hardkode Credentials

**Feil:**
```python
api_key = "sk_live_1234567890"  # ALDRI gjør dette!
```

**Riktig:**
```python
from client.config_manager import get_config_manager
config = get_config_manager()
api_key = config.get_api_key()
```

### ⚠️ Sett Riktige Filrettigheter

```bash
# Kun eier kan lese/skrive
chmod 600 ~/.printmaster/config.json

# Verifiser
ls -la ~/.printmaster/config.json
# Skal vise: -rw------- (600)
```

## Avansert Bruk

### Manuell Event Rapportering

```python
from client.event_reporter import EventReporter
from client.config_manager import get_config_manager

# Hent credentials fra config
config = get_config_manager()
reporter = EventReporter(
    base_url=config.get_backend_url(),
    api_key=config.get_api_key(),
    recipient_id=config.get_recipient_id()
)

# Send custom event
event_id = reporter.report_event(
    event_type="custom_event",
    printer_serial="01P00A123",
    printer_ip="192.168.1.100",
    event_status="info",
    message="Custom event message"
)

print(f"Event ID: {event_id}")
```

### Disable Event Reporting

For å midlertidig deaktivere event reporting:

**Metode 1: Fjern credentials fra config**
```bash
# Backup current config
cp ~/.printmaster/config.json ~/.printmaster/config.json.backup

# Edit config and remove api_key
# Event reporting vil automatisk deaktiveres
```

**Metode 2: Stopp klienten**
```bash
# Event reporting er kun aktivt når klienten kjører
# Stopp klienten for å stoppe event reporting
```

## Testing

### Unit Tests

```bash
# Test event reporter
python -m unittest client.tests.test_event_reporter -v

# Test HMS handler
python -m unittest client.tests.test_hms_handler -v
```

### Manual Testing

```python
# Test event reporting med test credentials
from client.event_reporter import EventReporter

reporter = EventReporter(
    base_url="https://test-api.com",
    api_key="test-key",
    recipient_id="test-recipient"
)

# Test event sending (vil feile mot test API, men tester logikken)
event_id = reporter.report_event(
    event_type="test_event",
    printer_serial="TEST123",
    printer_ip="192.168.1.1",
    event_status="info",
    message="Test event"
)
```

## Support

For problemer eller spørsmål:
1. Sjekk logs: `tail -f ~/.printmaster/logs/client.log`
2. Verifiser config: `cat ~/.printmaster/config.json`
3. Test connection: `curl -H "X-API-Key: your-key" https://backend-url/health`

---

**Viktig:** Husk alltid å lagre credentials i config-filen, aldri hardkode dem!
