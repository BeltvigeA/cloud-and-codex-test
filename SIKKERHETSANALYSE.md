# Sikkerhetsanalyse - PrintPro3D API

**Analysedato:** 2026-01-19
**Analysert av:** Claude Code
**API:** Cloud Printer Backend (Flask/Google Cloud Run)

---

## Innholdsfortegnelse

1. [Sammendrag](#sammendrag)
2. [Kritiske Sårbarheter](#kritiske-sårbarheter)
3. [Høy Risiko](#høy-risiko)
4. [Medium Risiko](#medium-risiko)
5. [Lav Risiko](#lav-risiko)
6. [Positive Funn](#positive-funn)
7. [Anbefalinger](#anbefalinger)

---

## Sammendrag

Denne analysen dekker PrintPro3D Cloud Printer Backend API, en Flask-basert tjeneste som kjører på Google Cloud Run. API-et håndterer 3D-printerjobber, filopplasting, printerkontroll og statusoppdateringer.

### Overordnet Risikovurdering: **MEDIUM-HØY**

| Kategori | Antall Funn |
|----------|-------------|
| Kritisk | 2 |
| Høy | 4 |
| Medium | 5 |
| Lav | 3 |

---

## Kritiske Sårbarheter

### 1. KRITISK: Uautentiserte Endepunkter med Sensitiv Data

**Lokasjon:** `main.py:1870-1897` (`/recipients/<recipientId>/pending`)

**Beskrivelse:**
Endepunktet `/recipients/<recipientId>/pending` krever INGEN autentisering og eksponerer:
- Ventende filer for en mottaker
- Fetch-tokens (kan brukes til å laste ned filer)
- Produkt-IDer
- Filnavn og metadata

**Kode:**
```python
@app.route('/recipients/<recipientId>/pending', methods=['GET'])
def listPendingFiles(recipientId: str):
    # INGEN API-nøkkel-validering her!
    # ...
    pendingFiles, skippedFiles = buildPendingFileList(firestoreClient, recipientId)
```

**Risiko:**
En angriper kan gjette eller brute-force `recipientId` (ofte UUID-er) og få tilgang til:
- Fetch-tokens som kan brukes til å laste ned G-code filer
- Informasjon om ventende printerjobber
- Metadata om produkter

**Anbefaling:**
Legg til `ensureValidApiKey()` validering på dette endepunktet.

---

### 2. KRITISK: Fetch-Token Eksponering i Responser

**Lokasjon:** `main.py:1614-1624` (buildPendingFileList)

**Beskrivelse:**
Fetch-tokens returneres i klartekst i API-responser og lagres ukryptert i Firestore:

```python
pendingFiles.append({
    'fileId': documentSnapshot.id,
    'originalFilename': metadata.get('originalFilename'),
    'productId': metadata.get('productId'),
    'fetchToken': fetchToken,  # Token eksponert!
    # ...
})
```

**Risiko:**
- Tokens kan snappes opp i nettverkstrafikk (selv med HTTPS kan logging eksponere dem)
- Ingen hashing av tokens i databasen
- Kombinert med uautentisert `/pending` endepunkt gir dette direkte filtilgang

**Anbefaling:**
- Hash tokens før lagring i Firestore
- Ikke returner tokens direkte; bruk en separat autentisert flyt for å hente tokens

---

## Høy Risiko

### 3. HØY: Manglende Rate Limiting

**Lokasjon:** Hele API-et

**Beskrivelse:**
Det finnes ingen rate limiting på noen endepunkter. Dette gjør API-et sårbart for:
- Brute-force angrep på API-nøkler
- Denial of Service (DoS) angrep
- Token-guessing på `/fetch/<token>` endepunktet

**Påvirkede endepunkter:**
- `/fetch/<fetchToken>` - kan brute-forces
- `/upload` - kan brukes til ressursutmattelse
- `/control` - kan floodes med kommandoer
- Alle autentiserte endepunkter - API-nøkkel brute-force

**Anbefaling:**
Implementer rate limiting med for eksempel:
- Flask-Limiter
- Cloud Armor (GCP)
- API Gateway rate policies

---

### 4. HØY: SSRF-Sårbarhet i Filopplasting

**Lokasjon:** `main.py:872-884`

**Beskrivelse:**
`/upload` endepunktet laster ned filer fra bruker-angitte URLer uten tilstrekkelig validering:

```python
gcodeUrl = payload.get('gcodeUrl')
# ...
downloadResponse = requests.get(
    gcodeUrl,
    headers=downloadHeaders,
    timeout=60,
)
```

**Risiko:**
- Server-Side Request Forgery (SSRF)
- Kan brukes til å scanne interne nettverk
- Kan treffe GCP metadata-endepunkter (f.eks. `http://metadata.google.internal/`)
- Kan brukes til å hente sensitive interne ressurser

**Anbefaling:**
- Valider URL-skjema (kun HTTPS)
- Blokker private IP-adresser (10.x.x.x, 192.168.x.x, 169.254.x.x, etc.)
- Blokker GCP metadata-endepunkter spesifikt
- Bruk en allowlist for tillatte domener hvis mulig

---

### 5. HØY: Informasjonslekkasje i Feilmeldinger

**Lokasjon:** Flere steder, f.eks. `main.py:118-132`

**Beskrivelse:**
Feilresponser inkluderer detaljerte traceback og interne detaljer:

```python
errorPayload = {
    'ok': False,
    'error_type': errorType,
    'message': message,
    'detail': detail,
    'traceback': tracebackText,  # Fullt stacktrace!
}
```

**Risiko:**
- Eksponerer intern kodestruktur
- Kan avsløre filstier og modulnavn
- Hjelper angripere med å forstå systemet

**Anbefaling:**
- Fjern `traceback` fra produksjonsresponser
- Logg detaljer internt, men returner generiske feilmeldinger til klienter

---

### 6. HØY: Manglende CORS-Konfigurasjon

**Lokasjon:** `main.py` (mangler helt)

**Beskrivelse:**
Det er ingen CORS-headers eller konfigurasjon i API-et. Avhengig av hvordan API-et brukes, kan dette føre til:
- Uautorisert tilgang fra ondsinnede nettsider
- Cross-Site Request Forgery (CSRF) angrep

**Anbefaling:**
Implementer eksplisitt CORS-policy med `flask-cors`:
```python
from flask_cors import CORS
CORS(app, origins=['https://tillatt-domene.com'])
```

---

## Medium Risiko

### 7. MEDIUM: Svak API-Nøkkel Validering

**Lokasjon:** `main.py:275-284`

**Beskrivelse:**
API-nøkkel-valideringen har flere svakheter:

```python
def ensureValidApiKey() -> Optional[Tuple[dict, int]]:
    if not validPrinterApiKeys:
        return None  # Hvis ingen nøkler er konfigurert, tillates alle!

    providedKey = getProvidedApiKey()
    if not providedKey or providedKey not in validPrinterApiKeys:
        return makeErrorResponse(401, 'AuthError', 'Invalid API key')
```

**Problemer:**
1. Hvis `validPrinterApiKeys` er tom, tillates ALLE forespørsler uten autentisering
2. Nøkler sendes via query-parameter (`?apiKey=xxx`) som kan logges i server-logger
3. Ingen nøkkelrotasjon eller utløpsdato-funksjonalitet

**Anbefaling:**
- Returner feil hvis ingen API-nøkler er konfigurert
- Kun tillat nøkler via `X-API-Key` header
- Implementer nøkkelrotasjon og overvåking

---

### 8. MEDIUM: Handshake-Endepunkt uten Autentisering

**Lokasjon:** `main.py:991-1111` (`/products/<productId>/handshake`)

**Beskrivelse:**
Handshake-endepunktet krever ingen autentisering og kan:
- Returnere fetch-tokens til uautoriserte parter
- Oppdatere filstatus i databasen
- Eksponere produktmetadata

**Anbefaling:**
Vurder å legge til autentisering eller implementer en separat "device registration" flyt.

---

### 9. MEDIUM: Potensielt Usikker Deserialisering

**Lokasjon:** `main.py:420-427`

**Beskrivelse:**
Parsing av nøkkel-verdi-strenger inkluderer `unicode_escape` dekoding:

```python
try:
    normalizedValue = bytes(rawValuePart, 'utf-8').decode('unicode_escape')
except UnicodeDecodeError:
    normalizedValue = rawValuePart
```

**Risiko:**
Kan potensielt utnyttes for escape-sekvens-injeksjon i visse kontekster.

**Anbefaling:**
Vurder om denne funksjonaliteten er nødvendig og stram inn parsingen.

---

### 10. MEDIUM: Ingen Input-Validering på recipientId

**Lokasjon:** Flere endepunkter

**Beskrivelse:**
`recipientId` brukes direkte i database-spørringer uten validering av format:

```python
fileQuery = firestoreClient.collection(firestoreCollectionFiles).where(
    filter=FieldFilter('recipientId', '==', recipientId)
)
```

**Risiko:**
- NoSQL-injeksjon (begrenset i Firestore, men mulig med spesielle operatorer)
- Enumeration av mottakere

**Anbefaling:**
Valider at `recipientId` matcher forventet format (f.eks. UUID).

---

### 11. MEDIUM: Manglende Content-Type Validering på Opplasting

**Lokasjon:** `main.py:850-865`

**Beskrivelse:**
MIME-type validering er valgfri og kan omgås:

```python
mimeType = payload.get('mimeType') or payload.get('mime_type')
if mimeType and mimeType not in allowedUploadMimeTypes:
    # Avvis kun hvis MIME type er oppgitt og ugyldig
```

**Risiko:**
Opplastinger uten MIME-type blir ikke validert.

**Anbefaling:**
Krev MIME-type og valider filinnhold i tillegg til metadata.

---

## Lav Risiko

### 12. LAV: Debug-Endepunkt i Produksjon

**Lokasjon:** `main.py:2663-2706` (`/debug/listPendingCommands`)

**Beskrivelse:**
Et debug-endepunkt er eksponert (krever API-nøkkel, men kan gi ekstra informasjon):

```python
@app.route('/debug/listPendingCommands', methods=['POST'])
def debugListPendingCommands():
```

**Anbefaling:**
Fjern eller deaktiver i produksjon.

---

### 13. LAV: Logging av Sensitiv Data

**Lokasjon:** Flere steder, f.eks. `main.py:2446-2450`

**Beskrivelse:**
Kommando-payloads logges i sin helhet:

```python
logging.info(
    'Claimed printer control command %s with payload=%s',
    commandId,
    json.dumps(jsonablePayload, ensure_ascii=False),
)
```

**Risiko:**
Sensitive data kan ende opp i logger.

**Anbefaling:**
Filtrer eller masker sensitive felt før logging.

---

### 14. LAV: Ingen Timeout på Database-Operasjoner

**Lokasjon:** Hele API-et

**Beskrivelse:**
Firestore-operasjoner har ingen eksplisitte timeouts, som kan føre til hengende forespørsler.

**Anbefaling:**
Implementer timeouts på database-kall.

---

## Positive Funn

API-et har flere gode sikkerhetspraksiser:

1. **KMS-Kryptering** (`main.py:897-908`)
   Sensitiv metadata krypteres med Google Cloud KMS før lagring.

2. **Secure Filename** (`main.py:829`)
   Bruker `werkzeug.utils.secure_filename()` for å sanitere filnavn.

3. **Fetch-Token TTL** (`main.py:920`)
   Fetch-tokens har begrenset levetid (standard 15 minutter).

4. **Engangs-Tokens** (`main.py:1346-1348`)
   Fetch-tokens kan kun brukes én gang (`fetchTokenConsumed`).

5. **Kryptografisk Sikre Tokens** (`main.py:628-629`)
   Bruker `secrets.token_urlsafe(32)` for token-generering.

6. **Firestore Transaksjoner** (`main.py:2140-2161`)
   Bruker transaksjoner for atomiske operasjoner ved jobb-claiming.

7. **Input Sanitering** (`main.py:305-338`)
   Konsekvent bruk av sanitering for streng-felt.

8. **Kommando-Routing Validering** (`main.py:2490-2516`)
   Validerer at kommandoer rutes til riktig mottaker/printer.

9. **Signerte URLer** (`main.py:1427-1471`)
   Bruker signerte URLs for GCS-filtilgang med 15-minutters utløp.

10. **Strukturert Logging** (`main.py:87-95`)
    God audit trail gjennom strukturert event-logging.

---

## Anbefalinger

### Prioritet 1 (Kritisk - Fiks Umiddelbart)

1. **Legg til autentisering på `/recipients/<recipientId>/pending`**
   ```python
   @app.route('/recipients/<recipientId>/pending', methods=['GET'])
   def listPendingFiles(recipientId: str):
       apiKeyError = ensureValidApiKey()
       if apiKeyError:
           return apiKeyError
       # ...
   ```

2. **Ikke eksponer fetch-tokens direkte i API-responser**
   - Bruk en separat autentisert flyt for token-henting
   - Hash tokens i databasen

3. **Fiks API-nøkkel bypass når ingen nøkler er konfigurert**
   ```python
   def ensureValidApiKey():
       if not validPrinterApiKeys:
           return makeErrorResponse(503, 'ConfigError', 'API keys not configured')
       # ...
   ```

### Prioritet 2 (Høy - Fiks Snart)

4. **Implementer rate limiting**
   - Bruk Flask-Limiter eller Cloud Armor
   - Spesielt viktig for `/fetch`, `/upload`, og autentisering

5. **Implementer SSRF-beskyttelse**
   - Valider URLer mot private IP-ranges
   - Blokker interne GCP-tjenester

6. **Fjern traceback fra produksjonsresponser**

7. **Konfigurer CORS eksplisitt**

### Prioritet 3 (Medium - Planlegg)

8. Valider `recipientId` format (UUID)
9. Krev MIME-type ved opplasting
10. Vurder autentisering på handshake-endepunkter
11. Implementer nøkkelrotasjon

### Prioritet 4 (Lav - Vurder)

12. Fjern debug-endepunkter i produksjon
13. Implementer masking i logger
14. Legg til timeouts på database-operasjoner

---

## Konklusjon

PrintPro3D API har et solid fundament med god bruk av kryptering, token-sikkerhet og input-validering. De mest kritiske sårbarhetene er:

1. **Uautentisert tilgang til sensitive endepunkter** som kan eksponere fetch-tokens
2. **Manglende rate limiting** som muliggjør brute-force og DoS
3. **SSRF-sårbarhet** i filopplastingsfunksjonen

Ved å adressere de kritiske og høy-risiko funnene vil API-ets sikkerhetsnivå forbedres betydelig.

---

*Denne rapporten er generert som en del av en sikkerhetsgjennomgang og bør brukes som utgangspunkt for videre sikkerhetstiltak.*
