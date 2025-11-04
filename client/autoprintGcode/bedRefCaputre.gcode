; ================================
; BED DOWN PULSES — Bambu P1/X1
; Motion: only downward steps (Z+), no upward moves
; Steps : 40 × (Z +5mm, wait 3s)  → total ~200 mm down
; Speed : F600 (600 mm/min = 10 mm/s)
; Notes : Start at Z=0 after homing; build height on P1/X1 is ~256 mm
;          so 200 mm is within limits. Adjust count/step if needed.
; ================================
M400                 ; flush planner
M17                  ; steppers on
G90                  ; absolute positioning
G28                  ; home all axes (Z=0 at top)
G4 S3                ; wait 3 s at home

G91                  ; RELATIVE moves from here
; ---- step 01/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 02/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 03/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 04/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 05/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 06/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 07/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 08/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 09/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 10/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 11/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 12/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 13/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 14/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 15/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 16/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 17/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 18/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 19/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 20/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 21/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 22/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 23/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 24/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 25/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 26/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 27/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 28/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 29/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 30/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 31/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 32/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 33/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 34/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 35/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 36/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 37/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 38/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 39/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s
; ---- step 40/40 ----
G0 Z5 F600          ; bed down 5 mm (increase Z)
G4 S3               ; wait 3 s

G90                  ; back to absolute (do not move)
M400                 ; flush planner
; End — bed is now ~200 mm lower than home