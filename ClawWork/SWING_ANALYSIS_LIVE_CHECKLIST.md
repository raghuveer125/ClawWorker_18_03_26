# Swing Analysis Live Integration Checklist

## Scope
- Keep existing historical upload flow.
- Add live mode with automatic feed updates.
- Add index switcher for `SENSEX` and `NIFTY50`.
- Add configurable sync interval from `1s` to `60s`.
- Keep candles available in both modes.

## Checklist
- [x] Add mode switch in UI: `Historical Mode` and `Live Mode`.
- [x] Preserve upload-based historical flow (signal CSV + OHLCV CSV).
- [x] Wire live feed using backend endpoints:
  - [x] `GET /api/fyersn7/dates`
  - [x] `GET /api/fyersn7/signals/{date}?index=...`
- [x] Add live interval control (bounded to `1..60` seconds).
- [x] Add live index dropdown (`NIFTY50`, `SENSEX`).
- [x] Auto-refresh live data on interval change and index change.
- [x] Keep export for detected swing points in both modes.
- [x] Keep candles toggle (`Candles ON/OFF`) in both modes.
- [x] Implement live candles by deriving OHLC from incoming live spot ticks.
- [x] Add live status text (rows, candles, interval, refresh status, last update).
- [ ] Optional: Add backend-native OHLC endpoint for exchange-grade candles.
- [ ] Optional: Add pause/resume live stream button.
- [ ] Optional: Add date selector for replaying historical days in live mode.

## Notes
- Live candles are currently derived from signal spot ticks grouped by minute.
- If backend OHLC is added later, frontend can switch from derived to native candles.
