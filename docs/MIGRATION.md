# Migrate from AppleSiliconThermals

Omafan 3.0.0 introduces a new name, standalone repository and plugin ID. Original
history and attribution are preserved. The previous fork remains archived;
use [kartikkabadi/omafan](https://github.com/kartikkabadi/omafan) for new installs
and updates.

| Item | Previous | Omafan |
| --- | --- | --- |
| Plugin ID | `io.github.lukasmoriarty.applesiliconthermals` | `io.github.kartikkabadi.omafan` |
| Directory under `~/.config/omarchy/plugins/` | `io.github.lukasmoriarty.applesiliconthermals/` | `io.github.kartikkabadi.omafan/` |
| Repository | `kartikkabadi/AppleSiliconThermals` | `kartikkabadi/omafan` |

## Replace the widget

Back up the old checkout and `~/.config/omarchy/shell.json` outside the watched
plugin directory. Keep only one enabled widget and one fan controller. Do not
uninstall control merely to change the widget name: that deliberately saves
Auto and removes its active preference.

For a normal plugin-managed installation:

```bash
omarchy plugin disable io.github.lukasmoriarty.applesiliconthermals
omarchy plugin add https://github.com/kartikkabadi/omafan.git --enable
omarchy restart shell
```

Verify the new widget displays your existing saved mode and profiles. Then
remove the disabled old plugin:

```bash
omarchy plugin remove io.github.lukasmoriarty.applesiliconthermals
```

Removing the old widget leaves its separately installed controller intact.
Run setup from the new checkout to upgrade root components; see
[Installation](INSTALL.md). Changing a Git remote alone does not change the
plugin ID in the bar layout.

An in-place migration may instead move the checkout to the new directory and
replace the old `id` in `shell.json`, preserving entry settings such as
`spinIcon`. Use the new manifest and module name, update the checkout's Git
remote to Omafan, and restart the shell. Do not leave both layout entries
enabled. The plugin-managed route above avoids hand-editing the layout;
custom placement/settings can be copied from the backup afterward.

## Stable privileged names

The rebrand retains these root-side names to reuse the installed controller
and durable profiles instead of creating a second controller:

| Purpose | Compatibility path/name |
| --- | --- |
| Root broker | `/usr/local/libexec/apple-silicon-fan-control` |
| Supervisor | `apple-silicon-fan-watchdog.service` |
| Pre-sleep guard | `apple-silicon-fan-sleep.service` |
| Post-wake hook | `/usr/lib/systemd/system-sleep/apple-silicon-fan-control` |
| Single-account policy | `/etc/sudoers.d/apple-silicon-fan-control` |
| Durable preference | `/var/lib/apple-silicon-fan-control/preference.json` |
| Upgrade backups | `/var/lib/apple-silicon-fan-control/backups/` |
| Runtime state | `/run/apple-silicon-fan-control/` |

A 2.5.x installation needs no preference rename or conversion. Active mode,
Linked RPM, Separate map and generation are retained. Runtime state is separate
from the durable preference.

Read-only controller diagnostics require no sudo:

```bash
/usr/local/libexec/apple-silicon-fan-control status
systemctl status apple-silicon-fan-watchdog.service --no-pager
journalctl -u apple-silicon-fan-watchdog.service -n 50 --no-pager
```

Journal visibility depends on local permissions. Legacy filenames are
compatibility details, not a reason to install two controllers.

## Older saved preferences

Schemas 1–3 migrate on read to schema 4 without rewriting the file. An older
active Linked speed seeds the Linked bank; an older active Separate map seeds
the Separate bank. Existing Separate memory is retained. History older versions
did not save starts empty. Current-schema writes follow an explicit user change.

Original unbounded or lease-based controllers need upgrading through current
setup before manual controls are available. Earlier validation entries describe
historical behavior and remain in [VALIDATION.md](../VALIDATION.md).
