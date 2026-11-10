# Contributing to Omafan

Small, focused changes are welcome. Describe the problem and the resulting
behavior, and include steps that another contributor can reproduce. For a
security issue, follow [SECURITY.md](SECURITY.md) before opening a public issue
or pull request.

## Development

Use a branch in your fork or local checkout. Python 3.10+ and Node.js are enough
for the isolated regression tests; no Python packages are required. Quickshell
and the installed Omarchy UI modules enable the additional Qt checks.

Run from the repository root:

```bash
python3 -m unittest discover -s tests -v
node --test tests/test_telemetry.cjs
bash -n helper.sh setup.sh uninstall.sh
sh -n system/apple-silicon-fan-wake
python3 -m py_compile thermal.py system/apple-silicon-fan-control system/install-path-safety.py
git diff --check
```

The 3.0.0 validation baseline is 245 tests: 211 Python and 34 JavaScript. The
Python count includes 10 Qt integration checks. These skip when Quickshell or
Omarchy UI modules are unavailable, including on the current CI workers. State
which checks ran and which skipped; do not present skipped checks as passes.

The tests use temporary fixtures and do not require root or change host fan
settings. Do not run the installer or write real sysfs controls from automated
tests. A fixture pass does not establish compatibility with every Mac, kernel,
desktop version or physical suspend/resume path.

## Changes that need particular care

- Keep production sysfs and privileged paths fixed. Test mocks may redirect
  fixtures; public broker arguments and environment variables must not do so.
- Preserve bounded, account-specific authorization. Do not grant all local users
  fan control or execute mutable checkout code through a passwordless root rule.
- For persistence changes, cover migration, failed writes, interrupted requests
  and both remembered profiles. Switching layouts must preserve the other
  profile, including when its remembered speed is lower.
- For control changes, cover missing sensors, fan inventory changes, per-fan
  limits, startup, sleep/wake and recovery failures. Separate simulated cases
  from checks performed on physical hardware.
- Use plain text for data displayed in QML, validate helper output and follow
  Omarchy's theme, accessibility and reduced-motion conventions.

The installed `apple-silicon-fan-control` names are compatibility identifiers.
A product rename alone is not a reason to change the broker, service or saved
preference paths. Preserve existing MIT notices and attribution when moving or
adapting code.

## Pull requests

Explain the trigger, the expected behavior and what changed. Include the actual
validation commands and results. For documentation-only changes, say that rather
than implying hardware tests were run. Mention any migration or installation
impact, and avoid unrelated formatting changes.

Bug reports should include the Mac model, kernel, Omafan, Omarchy, Quickshell and
Qt versions where known. Include a minimal reproduction and only relevant,
reviewed output. Never attach credentials, private environment dumps or an
unfiltered system journal.
