# Development and verification

Omafan uses QML/JavaScript, Python's standard library and small shell entry
points. It makes no runtime network requests or downloads. Its MIT lineage
is credited in the [README](../README.md) and [license](../LICENSE).

Run from a checkout:

```bash
python3 -m unittest discover -s tests -v
node --test tests/test_telemetry.cjs
bash -n helper.sh setup.sh uninstall.sh
sh -n system/apple-silicon-fan-wake
python3 -m py_compile thermal.py system/apple-silicon-fan-control system/install-path-safety.py
git diff --check
```

Tests use isolated temporary hardware/runtime fixtures. They do not invoke
sudo, install services or write host fan controls. QML integration and rendered
motion cases run with Quickshell and Omarchy UI modules; they skip without those
modules. Ubuntu 22.04/24.04 CI covers portable Python/JavaScript checks and
structural service validation.

The last full review before the 3.0.0 rebrand passed 211 Python tests and 34
JavaScript tests, including local QML/motion cases. [VALIDATION.md](../VALIDATION.md)
records historical revisions, CI links, physical checks and limits. Old entries
refer to older behavior, including the superseded UI heartbeat lease. A passing
fixture is not a physical reboot or suspend test.

## Review boundaries

Monitoring is unprivileged. The optional root broker accepts bounded literal
commands, validates JSON/root ownership, serializes writes and separates durable
intent from runtime state. It accepts no alternate executable/sysfs paths from
user arguments or environment. The sudo policy grants bounded user actions to
one account; lifecycle and supervision actions remain root-only.

Profile/recovery changes should test failed/interrupted commits, stale
callbacks, mode-switch ordering, Auto priority, changed inventory/limits,
invalid sensors, sleep epochs and repeated driver refusal. Keep tests isolated
from real root state and host sysfs writes.

Keep displayed sensor/model text plain; validate subprocess data before UI use.
Preserve English fallback catalogs and accessible button semantics. Icon motion
should retain phase through RPM updates and respect reduced motion.

Tests and supervision reduce known failure modes. They do not establish support
for every Apple Silicon model or guarantee cooling under kernel/hardware faults.
