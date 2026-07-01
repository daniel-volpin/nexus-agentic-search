# Security Policy

## Supported Versions

Security fixes are targeted at the latest released version on `main`.
Older versions may not receive patches.

## Reporting a Vulnerability

Do not open a public GitHub issue for suspected vulnerabilities.

Report security issues privately to the maintainer and include:

- a clear description of the issue
- affected versions or commit range
- reproduction steps or a proof of concept
- impact assessment if known

Current reporting path:

- contact the maintainer privately through the repository owner profile
- if GitHub Security Advisories are enabled for the repository, prefer that channel for coordinated disclosure

Keep the initial report non-public until triage is complete.

## Disclosure Expectations

- You will receive an acknowledgement when the report is reviewed.
- Valid reports will be triaged and fixed as capacity allows.
- Public disclosure should wait until a fix or mitigation is available.

## What To Avoid In Reports

- do not include real secrets or third-party credentials
- do not publicly post exploit details before a fix window exists
- do not mass-scan or stress shared third-party infrastructure on behalf of this project

## Scope Notes

This project is security-sensitive by design. Reports involving these areas are especially useful:

- transport authentication
- crawl SSRF protections
- citation validation
- prompt / secret redaction boundaries
- deployment defaults that could expose internal surfaces
