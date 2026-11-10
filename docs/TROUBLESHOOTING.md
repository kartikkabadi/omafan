# Troubleshooting

## No readings or fan controls

Omafan requires Linux's `macsmc_hwmon` driver. macOS and generic PCs are
unsupported. Fanless Macs can show available readings without fan controls.
Missing sensors, limits or driver access produce a diagnostic rather than
guessed values. A missing reading is different from zero.

From the plugin checkout, collect the bounded read-only output:

```bash
./helper.sh get
```

When [reporting a problem](https://github.com/kartikkabadi/omafan/issues), include
the error, model, kernel, Python, Omarchy, Quickshell and Qt versions. Review
output before sharing it; omit credentials and private environment dumps.

## Manual is unavailable after updating

Updating the widget does not update root components or permissions. Review
and rerun `./setup.sh --enable-manual-control` as your normal user, then restart
the shell. Outdated capabilities or command permissions keep numeric controls
disabled. Do not bypass this by allowing arbitrary policy arguments.

If setup reports an unsafe install directory, inspect that exact path and its
physical symlink targets. Do not recursively chmod system directories or rerun
the whole script as root. Setup may leave some root files after a failure;
service readiness is required before new control permission is granted.

## Saved settings differ from actual control

Saved means the preference was acknowledged. Hardware/sensor faults can pause
application while preserving it; the popup distinguishes these states and
shows why. Recovery waits for valid, cool readings and a cooldown. Physical
fan speed also takes time to settle.

Repeated target mismatch can mean driver/firmware refusal or another
controller. Stop conflicting tools and inspect requested/observed targets in
the diagnostic. Do not force repeated writes through a temperature pause.
See [recovery behavior](CONTROL.md#saved-intent-and-safety-pauses) and
[read-only service diagnostics](MIGRATION.md#stable-privileged-names).

A failed Auto save blocks runtime restoration of the older manual setting,
but cannot change the last acknowledged reboot preference on unwritable
storage. Resolve the error and successfully choose Auto before treating it
as remembered.

## Separate cannot restore after hardware changes

Per-fan maps must match detected indices exactly. An inactive obsolete map
does not disable healthy Linked control. Choose Auto and explicitly configure
current channels to replace it; a per-fan helper edit can seed a new current
map. Old speeds are never silently assigned to different fans. See
[changed hardware](CONTROL.md#transactions-and-changed-hardware).

## The popup looks outdated, duplicated or stuck

Run `omarchy restart shell` after updates adding QML types. Saved control stays
intact. For duplicate widgets, follow [Migration](MIGRATION.md) and remove the
old entry.

Failed/hung helpers are terminated and late callbacks ignored. Queued Auto is
retried after successful periodic telemetry, not in a tight loop. Persistent
failures stay visible. A shell restart alone does not prove Auto was saved;
check the resulting status.

## Removal failed

Keep the controller available if `./uninstall.sh` cannot verify firmware
release. Resolve the error before deleting it. Removing just the widget leaves
saved manual control running. Follow the complete [removal procedure](INSTALL.md#remove).
