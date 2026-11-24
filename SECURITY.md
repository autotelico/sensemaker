# Security Policy

## Supported versions

Security fixes will be provided for the latest published SenseMaker release.
Development builds and modified copies are not supported.

## Reporting a vulnerability

**Do not publish security-sensitive details in a public issue, discussion, or
social post.**

Please report privately through GitHub's private vulnerability reporting:

https://github.com/autotelico/sensemaker/security/advisories/new

If that link is unavailable, open a minimal public issue asking for a private
channel and do not include sensitive details.

Please include:

- the affected SenseMaker version and Windows version;
- a concise description of the issue and its impact;
- reproduction steps or a proof of concept, if safe to share;
- whether audio, transcripts, local history, or update integrity are affected.

Do not send real recordings, transcripts, credentials, or other personal data.
Use synthetic or redacted samples.

## Update integrity

Release installers should be Authenticode-signed and published with SHA-256
checksums. The application only supports model downloads from the pinned
revisions in `assets/model_manifests.json`; every model file is verified before
use.

## Scope

Relevant areas include the desktop application, installer, model-download and
verification logic, local transcript-history encryption, and privacy controls.
Third-party vulnerabilities should normally be reported to the relevant
upstream project as well.
