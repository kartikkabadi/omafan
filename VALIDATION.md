# Omafan 3.0.1 submission review — 10 November 2026

- Reviewed the broker, telemetry/validation, QML controls, service units, sudo
  policy, installer/removal paths, saved profiles, attribution and listing assets.
- Hardened runtime trust checks to inspect intermediate symlink hops, including
  a detour through a writable directory which resolves back into a secure path.
- Tightened schema and health timestamp validation. Unsupported manual fan
  counts/indices now fail before manual target writes; Auto recovery still covers
  every discovered fan. Fractional hardware limits cannot enable UI controls.
- Added isolated regressions for these cases. Saved-profile schema and memory
  semantics are unchanged; no host fan target or preference was written by review.
- Local verification: 219 Python tests (including QML/motion cases), 35 JavaScript
  tests, shell syntax, Python compilation, manifest validation, service-unit
  verification and whitespace checks.
- The revised telemetry reads the physical M1 Pro's active Linked/Max preference
  correctly: saved 6241 RPM, driver targets 5779/6241, remembered Separate
  2300/2500, with no preference/control error. Historical hardware-write checks
  below describe their original revisions. Other models and physical reboot,
  suspend and hibernate remain unverified.

---

# Omafan 3.0.0 rebrand — 10 November 2026

- Rebranded the project and widget to Omafan under `kartikkabadi/omafan`.
  Canonical plugin ID: `io.github.kartikkabadi.omafan`.
- Preserved the full upstream and continuation history, original MIT copyright,
  explicit attribution, and a history-compatible move notice for the old fork.
- Added project artwork, focused user/developer guides, issue forms, private
  security-report guidance and a changelog.
- Both remembered profiles and the existing privileged controller/storage names
  remain compatible. Only display metadata and documentation changed; fan
  control, persistence schemas, safety and animation behavior are unchanged.
- Migrated the installed user plugin and its exact bar entry to the new ID and
  canonical Git remote, with private backups outside plugin discovery. Root
  controller paths and both saved profiles were retained. Only service display
  descriptions were refreshed; the controller was not restarted by the rebrand.
- At the user's request, captured real Linked, Separate and Auto views. Separate
  restored 2300/2500, Auto retained both profiles, and Manual restored Linked/Max
  6241 afterward (hardware targets 5779/6241), with no control or preference error.
  This also physically exercises the two-profile round trip. Screenshots are
  cropped to the application panel and contain no surrounding desktop content.
- All 211 Python tests and 34 JavaScript tests pass locally. Markdown references,
  SVG XML, plugin manifest and shell syntax checks pass. Hardware checks and
  limits from the preceding reviews remain applicable.

---

# Version 2.5.1 final full review — 10 November 2026

Reviewed the broker, telemetry/helper, all QML components, install/remove flows,
service units, sudo policy, stored profiles, test coverage and CI configuration.
Checked the live installation without changing the user's chosen RPM profiles.

Findings fixed:

- P1: An existing writable or user-owned install ancestor could let someone
  replace a root executable covered by the sudo policy. Setup and uninstall now
  run a shared read-only boundary check before elevation, including logical and
  physical symlink chains. This host's original paths were already secure.
- P2: A failed/timed-out pre-sleep service could skip ExecStop and strand the
  inhibit after wake. A root-only, post-only wake hook records a nonblocking
  boot-scoped event; the supervisor handles it after wake. Generations and sleep
  epochs prevent stale notifications from clearing a later inhibit. A separate
  failure-only service cleanup handles aborted attempts with no sleep hook;
  restoration requires both an ended sleep target and no queued sleep job.
  This follows the intermediate-wake behavior in [systemd's sleep implementation](https://github.com/systemd/systemd/blob/v262.1/src/sleep/sleep.c).
- P2: Missing or unresponsive helpers could strand queued Auto or block future
  telemetry. Process sequencing, termination and bounded periodic Auto recovery
  now cover failed starts, hung children and late callbacks.
- P3: Host button instances lacked names, selected states and activation actions
  for assistive technology. A local wrapper supplies those semantics; offscreen
  keyboard and assistive activation tests exercise the actual control handlers.

Validation:

- All 211 Python tests and 34 JavaScript tests pass on the review host. This
  includes nine UI integration cases, rendered icon motion, 108 broker cases,
  32 independent integration cases and 16 installer/elevation cases.
- New regressions reproduce the original installer boundary, missing/hung helper,
  failed/timed-out/aborted sleep and intermediate-wake bugs. Wake notification
  was also tested while an actual operation flock was held, without host fan I/O.
- Real offscreen Return/Space and assistive button presses exercise actual UI
  handlers. Hidden and disabled activation stays blocked.
- Shell syntax, Python compilation, manifest validation, sudoers parsing,
  structural systemd unit checks and diff checks pass. The new path preflight
  and bounded read-only sleep-target query also pass against this live host.
- GitHub Actions passed on Ubuntu 22.04 and 24.04 at `723e5da`:
  https://github.com/kartikkabadi/AppleSiliconThermals/actions/runs/42809392644
  The initial run exposed a Python 3.10 pathlib test-fixture seam, corrected by
  mocking Path metadata methods consistently across supported Python versions.
  Production code was unchanged by that fixture correction; Qt cases skip on
  CI runners without Omarchy, while all ten Qt cases passed on the review host.
- Installed version 2.5.1, its root-owned post-wake hook and updated sleep unit.
  Installed broker/hook/unit bytes match the reviewed source. Broker and hook
  are root:root 0755, sudo policy root:root 0440, preference root:root 0644.
  Both services remain enabled; the supervisor is active/running with no
  automatic restarts, pause, preference error or control error.
- The supervisor and shell restarted successfully. The original preference
  generation remained unchanged: active Linked/Max request 6241 (hardware
  targets 5779/6241), remembered Separate targets 2300/2500. No live profile
  switching, simulated heat, injected driver failure, reboot or sleep was run.
- Physical suspend/hibernate/reboot and other Mac models remain untested. Their
  state transitions have isolated fixture coverage; that is distinct from a
  physical hardware test. The sensors remain component readings, not CPU/GPU
  die temperatures. No remaining high-confidence blocker was found in this pass.

The reviewed runtime performs no Internet requests and needs no downloaded
Python dependencies. The root
broker uses fixed paths, isolated Python, bounded literal arguments, validated
JSON and root-owned state. These checks are not a guarantee against every future
bug, kernel/hardware fault, or overheating failure.

---

# Version 2.5 independent Linked memory — 10 November 2026

- Schema 4 adds a separate remembered Linked RPM. Linked edits update that bank;
  Separate edits and Auto retain it. Switching to Linked restores it even when
  it is lower than the Separate targets. First use without history uses the
  highest current target.
- The bounded, single-token `linked` action performs restoration under the same
  lock and write verification as the other manual commands.
- Schema 1/2/3 migration is read-only. Older active Linked settings seed the new
  bank; the Separate memory introduced in schema 3 is retained.
- All 177 Python tests (including five control QML cases and rendered icon motion)
  and 34 JavaScript tests pass locally. Script syntax, Python compilation,
  manifest validation, sudoers parsing and diff checks pass.
- Tests cover distinct profiles and repeated round trips, restoring a lower
  Linked RPM, exact bank validation, failed commits preserving both banks,
  cold-boot and Auto retention, queued restores followed by edits, and rapid
  layout cancellation. Independent review found no remaining blocker.
- Permission probes are individually bounded at 0.6 seconds; four probes plus
  the 1.5-second status read remain within the widget's 5-second read deadline.
  Mutation deadlines are unchanged.
- Production commit `0bd502e` passed GitHub Actions on both Ubuntu targets:
  https://github.com/kartikkabadi/AppleSiliconThermals/actions/runs/42804058131
- Installed the new broker/policy and live plugin, restarting the supervisor and
  shell. Migration reports Linked memory 6241 and Separate memory 2300/2500;
  the existing schema 3 preference file and generation remain unchanged until
  the next explicit user setting change.
- During supervisor startup, one readback briefly observed fan 2 at 2502 instead
  of requested 6241. Existing recovery paused manual application, retained both
  profiles, then restored the original 5779/6241 targets after its cooldown.
  Final status was ready/manual, with both memory capabilities enabled and no
  pause, preference error or control error. No additional forced retry was run.
- Actual layout switching and cold-boot restore were tested in isolated hardware
  fixtures and QML flow tests. The live session was not rebooted or switched
  away from the user's active Linked/Max preference.

---

# Version 2.4 remembered Separate profile — 10 November 2026

- Schema 3 stores inactive Separate targets independently of the active Linked
  target. Shared presets and explicit Auto preserve that remembered map.
- The bounded `separate` action restores the whole map in one locked, verified
  transaction. With no prior profile, it captures current per-fan setpoints.
- Schema 1/2 preferences migrate on read without file writes; an older active
  individual map also supplies its remembered profile.
- An inactive profile with a changed fan inventory does not disrupt Linked
  control. Restoring it fails without partial application or channel remapping.
- All 160 Python tests and 29 JavaScript tests pass locally. Python includes
  five control QML cases and the rendered icon-motion case.
- Memory regressions cover unequal targets through Linked changes and widget
  recreation, exact non-step RPM, queued restore followed by a fan edit, quick
  toggle cancellation, Auto precedence, cold boot and service/sleep recovery,
  failed commits, schema migration and strict malformed-profile rejection.
- Script syntax, Python compilation, manifest validation, sudoers parsing and
  diff checks pass. Independent review found no remaining blocker.
- Production commit `ab9137d` passed GitHub Actions on both Ubuntu targets:
  https://github.com/kartikkabadi/AppleSiliconThermals/actions/runs/42802306084
- Installed the schema 3 broker and exact-token policy. Setup backed up the
  existing preference and preserved the active Linked/Max request (6241 RPM,
  hardware-clamped to 5779/6241).
- Recovered the known earlier Separate pair (2300/2500) into inactive memory
  using the installed broker's lock, validators and atomic preference writer.
  Existing nonempty memory would have been left intact. Active mode, RPM,
  generation and hardware targets were unchanged by this metadata migration.
- Updated the live plugin and restarted the shell, then restarted the supervisor.
  The same active Linked/Max request, generation and remembered Separate pair
  survived. Final supervisor status was ready/manual, with no preference error,
  pause or control error. The new `separate` token passed a read-only sudo probe.
- No physical reboot/suspend or live Separate-speed test was performed; cold
  boot and target restoration are covered by isolated fixtures and QML flow tests.
- No additional executable paths, file paths or unbounded arguments are exposed
  through the single-account sudo policy.

---

# Version 2.3 linked controls and icon polish — 10 November 2026

- Linked manual controls provide one slider and Quiet/Balanced/Max presets.
  Separate controls retain the independent sliders. The durable broker control
  mode determines the layout after widget recreation.
- Fan motion now accumulates frame time at a fixed rate, independent of RPM
  polling. A rendering test changes RPM every 35 ms and checks phase continuity,
  the unchanged 16-pixel slot and 4% glyph scale, pause/resume, stopped fans,
  upright warning glyphs, and Omarchy's reduced-motion preference.
- 140 Python tests (including five control QML cases and one rendered motion
  case) and 24 JavaScript tests pass locally. Script syntax, Python compilation,
  manifest validation and diff checks pass.
- Mode-switch fixtures cover exact non-step targets, recreation in both layouts,
  queued edit cancellation, no-lowering transfer, Auto precedence, safety aborts,
  and release events from hidden or disabled sliders.
- GitHub Actions passed for production commit `d32554e` on Ubuntu 22.04 and
  24.04: https://github.com/kartikkabadi/AppleSiliconThermals/actions/runs/42799398823
  (QML cases skip without installed Omarchy modules).
- Updated the installed plugin to 2.3 and restarted the shell. The previous
  independent 2300/2500 targets and Separate layout remained intact. Selecting
  Linked in the live popup saved 2500 for both channels; actual sysfs targets
  confirmed 2500/2500. A second shell restart preserved the same shared request
  and generation, with the supervisor continuously active and no errors.
- The live Linked panel rendered a single slider, all three RPM-labelled presets,
  the layout selector, remembered indicator, and both fan telemetry cards.
  Subsequent live user interaction selected shared Max (6241 requested, clamped
  to 5779/6241); that latest choice was left unchanged.
- No machine reboot or suspend was performed; reboot remains fixture-tested.
- Hardware control, privilege policy and root service code are unchanged by 2.3.
  Existing independent targets are preserved until the user selects Linked.

---

# Version 2.1 persistence and UI validation — 10 November 2026

Implementation: `f47c4f3`, authentication fix `ade5c71`, readability `cde2e97`.

- 110 Python tests and 18 JavaScript tests pass locally. Python coverage includes
  three headless QML cases. Both Ubuntu CI targets pass (QML skips when Omarchy
  modules are not present).
- Independent fixtures simulate a new boot ID and empty runtime directory while
  retaining the durable preference; manual RPM is restored without a widget.
- Additional checks cover long operation without UI heartbeats, internal release
  preserving intent, explicit Auto transaction ordering, interrupted/failed saves,
  restoration blocks, corrupted and insecure preferences, parent-directory fsync,
  thermal hysteresis, pause/retry cooldowns, and restart/resume recovery.
- Installer elevation routing is tested with mocks: sudo authentication failures
  use pkexec without an unsupported separator; commands that ran and failed are
  never executed again through pkexec.
- Installed the persistent supervisor and root-owned preference on the M1 Pro.
  The prior shared request of 6241 RPM was saved, with per-fan targets 5779/6241.
- A real supervisor restart changed its process PID and restored that saved
  manual request. A real Omarchy shell restart left the same preference and
  manual control active. No UI heartbeat is required by version 2.1.
- The popup was inspected on the live desktop: fan RPM/target cards, percent
  bars, temperature/power cards, mode selector and remembered presets rendered.
  Numeric readouts were increased after inspection for better readability.
- New QML types did not replace an already compiled older widget during the
  first hot update. A full shell restart loaded the new UI; README documents
  this one-time upgrade requirement. Persistent fan control is independent of it.

The machine was not rebooted or suspended during this work. Reboot restoration
is covered by isolated cold-start fixtures; service and shell restarts were
physically tested. Other Mac models/kernels remain unverified. Hardware faults
can temporarily pause manual control while preserving the saved choice.

---

Earlier version 2.0 checks follow for history. Their lease-expiry behavior was
intentionally replaced by service-owned persistent settings in version 2.1.

# Validation: 10 November 2026

Implementation commit: `97b5e83` (version 2.0.0).

## Automated checks

- 68 Python tests passed locally, including 2 headless QML integration tests.
- 16 JavaScript validator/security cases passed with Node's test runner.
- Shell syntax, Python compilation, Omarchy manifest validation and diff checks passed.
- Both systemd units passed structural verification with the executable path
  substituted in temporary files; after installation the real units loaded.
- GitHub Actions succeeded on Ubuntu 22.04 and Ubuntu 24.04. QML tests skip on
  runners without the installed Omarchy UI modules.

Coverage includes fanless/unsupported/missing-driver states, sparse sensors,
zero/subzero and invalid readings, multiple fans with unequal limits, malformed
JSON, markup-shaped labels, missing executables, process recovery, command
serialization and Auto priority, generation-bound heartbeats, root ownership,
symlinks/FIFOs, actual flock contention, interrupted writes, partial failures,
watchdog expiry, hot sensors, external target changes and sleep inhibition.

## Physical machine checks

Tested on a 14-inch 2021 M1 Pro MacBook Pro, Arch Linux ARM kernel
7.2.11-14-1-ARCH, Omarchy 4.5.0, Qt 6.12 and Quickshell 0.3.2.

- Installed the root-owned broker, per-account sudoers and both service units.
  Setup established Auto before granting the per-account permission.
- The service started and reported ready/Auto without errors.
- A bounded maximum request was clamped to fan 1's 5779 RPM and fan 2's 6241 RPM.
- A generation-bound heartbeat renewed that lease. With no more heartbeats,
  the service restored Auto after 14 seconds (12-second TTL plus watchdog tick).
- The live bar widget's Max button successfully entered manual mode and renewed
  its own lease for longer than 12 seconds. Both displays showed the widget.
- Killed only the watchdog's main process with SIGKILL during that maximum-speed
  request. systemd restarted it and restored ready/Auto with a new PID after
  2 seconds. The widget stopped renewing the obsolete generation.
- The popup displayed both fans, correct hardware limits and component readings
  without the previous Qt color errors. The Auto and Close buttons worked.
- Final state: ready=true, mode=auto, inhibited=false, error=null; popup closed.
  The watchdog and pre-sleep guard remain enabled for subsequent use.

## Limits

Real machine suspend/resume, reboot, logout/login, unplugged-monitor transitions,
other Mac models and other kernel/Omarchy versions were not physically tested.
Sleep inhibition, stale generations, reboot identity and failed recovery have
fixture coverage, which is distinct from physical testing. The source and
kernel design do not expose CPU/GPU die temperatures or independently attest
mode changes made by other fan controllers. The watchdog is software recovery,
not a guarantee against kernel/hardware failure or overheating.
