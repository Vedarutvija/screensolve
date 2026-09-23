# Phase 3 — Desktop hotkey agent
## Goal
Python agent: global hotkey (default Ctrl+Alt+S), full-screen capture (mss/PIL, active-window fallback), POST to server, status feedback (capturing→uploading→accepted/failed). Config file for endpoint + hotkey. Silent otherwise.
## Acceptance Criteria
- [ ] Hotkey press on desktop creates a new dashboard card within seconds
- [ ] No capture without hotkey
## Edge cases
Server unreachable → log and keep running; multi-monitor → capture primary.
