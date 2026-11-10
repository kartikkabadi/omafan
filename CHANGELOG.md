# Changelog

## 3.0.0 — 2026-11-10

### Omafan

- New project identity, standalone repository, artwork and documentation.
- New Omarchy plugin ID: `io.github.kartikkabadi.omafan`.
- Original MIT copyright and upstream attribution retained; Git history preserved.
- Existing controller paths and saved profiles remain compatible. Follow the
  [migration guide](docs/MIGRATION.md) when upgrading an existing installation.

### Included from the 2.x work

- Independent Linked and Separate settings, remembered across restarts and reboot.
- Per-fan sliders and Quiet/Balanced/Max presets, live gauges and component readings.
- Constant-rate fan animation, reduced-motion support and accessible controls.
- Supervised cooling, bounded drift recovery, sleep/wake handling and safe failure paths.
- Validated local telemetry, strict privilege boundaries and regression coverage.

See [validation notes](VALIDATION.md) for test results, physical checks and limits.
