# Security

Omafan includes an optional privileged fan-control service. Its authorization,
installation paths, saved settings and recovery behavior are security-sensitive.
Monitoring does not require administrator permissions.

## Report a vulnerability privately

Use GitHub's [private vulnerability report form](https://github.com/kartikkabadi/omafan/security/advisories/new).
Do not publish exploit details, proof-of-concept code or sensitive logs in a
public issue or pull request before coordinating privately.

If the private form is unavailable, open a minimal public issue asking the
maintainer to enable private reporting or provide a private contact route.
Do not include the vulnerability details in that issue. There is no published
response-time or disclosure deadline commitment.

A useful private report includes:

- The affected version or commit and relevant installation details.
- The privilege or trust boundary involved and the expected impact.
- Minimal reproduction steps and any prerequisites.
- A suggested fix, if available, and whether the issue was tested on a fixture
  or physical hardware.

Review attachments before sending them. Remove credentials, tokens, unrelated
personal data and full environment or journal dumps. Do not change real fan
controls merely to demonstrate a vulnerability.

## Scope and support

Review focuses on the current main branch and latest release; a fix may require
upgrading. Report cooling or reliability failures through the bug template when
they do not expose a security vulnerability. If unsure, use private reporting.

RPM limits, component-temperature checks and software recovery reduce specific
failure modes. They do not guarantee safe cooling under every hardware, kernel
or firmware failure. The available component temperatures are not CPU/GPU die
temperatures. See the [README](README.md) for operating limits and removal steps.
