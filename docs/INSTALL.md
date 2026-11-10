# Install and remove Omafan

## Requirements

Omafan needs Linux with `macsmc_hwmon`, Python 3.10+ at `/usr/bin/python3`,
and Omarchy's Quickshell shell. Its UI uses `qs.Ui.PopupCard`, `OpticalGlyph`,
`Commons.Translations` and `Commons.I18n`. See the [tested versions](../README.md#compatibility-and-limits).
This is an Omarchy plugin, not a standalone Quickshell application.

Manual control additionally requires systemd as PID 1, sudo 1.9.10+ with
argument regular expressions, and root-owned sysfs controls. Installation from
a noninteractive process may need `pkexec` and a desktop authentication agent.
Unsupported hardware and missing limits disable controls rather than inventing
an RPM range. No Python packages need to be installed.

## Monitoring

```bash
omarchy plugin add https://github.com/kartikkabadi/omafan.git --enable
```

Plugin ID: `io.github.kartikkabadi.omafan`. The installed checkout is normally
`~/.config/omarchy/plugins/io.github.kartikkabadi.omafan/`.
Click the bar icon for actual fan speed, setpoints, limits and available readings.
Monitoring needs no root setup.

For a local checkout, place the complete repository in that directory, then:

```bash
omarchy plugin enable io.github.kartikkabadi.omafan --section right
```

## Optional manual control

Read the [control and safety notes](CONTROL.md). From the reviewed checkout,
run as your normal desktop user:

```bash
./setup.sh --enable-manual-control
```

Do not run `sudo ./setup.sh`. The installer handles authentication: interactive
terminals use sudo; noninteractive calls try `sudo -n`, with `pkexec` used only
if sudo requires authentication. A command that executed and failed is not
retried through another elevation tool.

Setup checks root ownership and permissions along installed paths, including
symlink targets, before elevation. It refuses unsafe existing directories
without silently changing their ownership. It backs up an existing preference,
installs the controller and wake handling, and verifies a known cooling state
before granting the installing account bounded fan-control permission. Other
local accounts are not automatically granted access.

If setup fails, inspect the diagnostic before retrying. Some root files may
already have been installed; failure does not imply rollback. Final permission
is granted only after service readiness. Use the removal procedure when
appropriate instead of deleting the executable by hand.

## Update

```bash
omarchy plugin update io.github.kartikkabadi.omafan
omarchy restart shell
```

A shell restart loads new QML types that a hot update may retain from the
previous version. It does not clear a saved manual setting.

Updating the plugin does not install or execute root code. If manual control
is installed, review the update and rerun `./setup.sh --enable-manual-control`
from the updated checkout to update the controller, services and policy. Setup
preserves acknowledged settings and makes a preference backup. Moving from the
old plugin ID has a separate [procedure](MIGRATION.md).

## Remove

With persistent control installed, run from its checkout before removing the
plugin:

```bash
./uninstall.sh
```

This verifies hardware release, saves Auto, disables services, verifies release
again, and removes the controller, policy, service/wake files and active saved
preference. Upgrade backups are retained. Removal stops on failed release:
keep the controller available and restore known cooling before deleting it.

Then remove the widget:

```bash
omarchy plugin disable io.github.kartikkabadi.omafan
omarchy plugin remove io.github.kartikkabadi.omafan
```

Monitoring-only installations need only those two commands. Removing or
disabling just the widget leaves an installed persistent controller and its
saved setting running.
