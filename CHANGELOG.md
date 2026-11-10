# Changelog

## 3.0.1 — 2026-11-10

- Check every intermediate directory symlink before trusting the installed broker at runtime.
- Reject non-integer broker schema versions and malformed watchdog-health timestamps.
- Refuse unsupported manual fan inventories before target writes while retaining firmware Auto recovery for all discovered channels.
- Reject fractional fan limits in the UI's integer RPM controls.
- Add regression coverage for these boundaries, root README removal instructions, artwork provenance and a marketplace preview.
- Qualify the marketplace name as Omafan (Apple Silicon) to distinguish unrelated same-named projects; the app branding and plugin ID are unchanged.
- Retain the existing saved-profile format and independent Linked/Separate settings.

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
