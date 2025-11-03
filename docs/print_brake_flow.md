# Brake Plate Demo Flow

```mermaid
flowchart TD
  A[Job payload received] --> B{enable_brake_plate == true<br/>OR plates_requested > 1?}
  B -- no --> Z[Run as normal]
  B -- yes --> C[Record checkpoints: 0%, 33%, 66%, 100%]
  C --> D[Print completes]
  D --> E[Ensure bed temp ≤ 30°C (poll Bambu state)]
  E --> F[Home XY (best-effort via Bambu API)]
  F --> G[At each checkpoint: capture image]
  G --> H[Compare locally vs reference slice<br/>(perceptual hash + histogram)]
  H --> I{All checkpoints clean?}
  I -- yes --> J[Proceed to next print]
  I -- no --> K[Brake attempt + re-check]
  K --> L{Attempts < 2?}
  L -- yes --> E
  L -- no --> M[Pause this printer only, report error to GUI/Base44, email alert]
```
