# Controls and recovery

## Auto, Linked and Separate

Auto saves firmware-managed cooling while retaining both manual profiles.
Manual opens Linked by default. Linked provides one RPM slider and
Quiet/Balanced/Max presets; Separate provides a slider and presets for each fan.
Changing one fan preserves the others.

Each layout has its own durable profile. Switching to Linked restores its last
RPM, even if lower than current Separate settings. Switching to Separate
restores its per-fan map. Editing either profile leaves the other intact. With
no history, Linked starts from the highest current target and Separate captures
current per-fan targets.

The root service saves the selected mode and both profiles independently of
the widget. Closing the popup, disabling the widget or restarting the shell
does not change them. On service restart or reboot it first resets to Auto,
then validates hardware and sensors before restoring a saved manual choice.
Choose Auto to save firmware control instead.

## RPM and readings

Requests are integer RPM from 1000 through 7999, clamped to each fan's mechanical
limits. Linked uses the widest detected range; fans with different maximums can
therefore have different physical targets from one shared request. Separate
uses each fan's own limits. Quiet and Balanced use 25% and 50% of the relevant
maximum, clamped to the allowed range. These are fixed settings, not temperature
curves. Actual RPM can take a few seconds to reach a target.

Monitoring alone shows mode as unknown: Linux does not expose the underlying
SMC mode, and a positive setpoint does not prove manual operation. The service
reports its last successful Auto reset or verified manual application. It
cannot attest changes later made by unrelated tools; use one controller.

Temperatures are NAND, battery hotspot, charge regulator and Wi-Fi/BT component
readings, not CPU/GPU die temperatures. They can lag load. Maximum means the
maximum of available component readings. Missing data displays unavailable;
zero is valid.

## Saved intent and safety pauses

A hardware/sensor fault can return hardware to firmware while retaining the
manual preference. The popup shows saved intent separately from actual state
and explains pauses. Checks run every two seconds. Missing, malformed or hot
component readings pause manual control at 80°C; restoration needs valid
readings below 75°C and the retry cooldown. These sensors cannot replace
processor thermal management.

Transient target drift gets at most two verified repairs within 30 seconds.
Repeated refusal pauses manual control with requested/observed diagnostics and
a longer cooldown, avoiding continuous contention with firmware. systemd uses
an eight-second watchdog deadline to restart a stalled supervisor. Failure and
shutdown handling attempt Auto; software cannot guarantee success if the kernel
or hardware is unresponsive.

Sleep releases control and inhibits new manual writes. A root-only post-wake
event avoids waiting on locks held by frozen user processes. Recovery waits
for the outer sleep operation to finish, including intermediate
suspend-then-hibernate wakes. Failure cleanup covers aborted sleep attempts.
Internal release, sleep and shutdown preserve the preference. Physical
suspend/hibernate/reboot remain untested on the review machine; their
transitions have isolated fixture coverage.

## Transactions and changed hardware

The broker serializes requests, validates all channels, journals incomplete
operations as unknown and verifies readback. Numeric choices are acknowledged
as saved after hardware verification and an atomic preference commit with file
and directory synchronization. Healthy one-fan edits avoid rewriting unchanged
registers. Pending edits coalesce per fan; Auto cancels queued manual changes.

Explicit Auto is saved before hardware release, so an interrupted release
cannot revive an older manual preference. If saving Auto fails, its error stays
visible and runtime restoration is blocked. A failed durable write cannot
change the reboot preference: resolve the storage error before assuming the
choice is remembered. Corrupt/insecure preferences fail closed to Auto.

Separate maps must match detected fan indices exactly. A missing or new fan
pauses an active map rather than reassigning a channel's RPM. An obsolete
inactive Separate map does not disrupt Linked, but restoring it is refused.
Select Auto, then explicitly configure current channels again. Changing limits
replans the clamp while preserving raw requested values.

Bounded helper commands, from the plugin checkout:

```text
./helper.sh get
./helper.sh set auto
./helper.sh set 3000
./helper.sh set-fan 1 3000
./helper.sh set-linked
./helper.sh set-separate
```

Only `get` is read-only; the others change or restore saved settings. From
Linked or Auto, a per-fan edit captures a complete current map and can replace
an obsolete Separate profile. Indices are bounded to 1–64; arbitrary paths and
executables are not accepted. Root-side compatibility names and storage details
are in [Migration](MIGRATION.md).

## Appearance and accessibility

The fan icon rotates at a constant rate independent of RPM polling and follows
Omarchy's reduced-motion preference. To disable it specifically, set
`"spinIcon": false` on Omafan's entry in the `bar.layout` configuration of
`~/.config/omarchy/shell.json`:

```json
{ "id": "io.github.kartikkabadi.omafan", "spinIcon": false }
```

The popup scrolls on smaller screens, supports Escape and keyboard focus, and
exposes button names, selected states and activation actions to assistive
technology. English strings are supplied; the selected Omarchy language falls
back to English until a matching catalog is contributed. Sensor/model labels
are plain text.
