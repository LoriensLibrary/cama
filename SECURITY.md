# Security Policy

CAMA is independent research software maintained by [Lorien's Library LLC](https://lorienslibrary.netlify.app). It is not a production service or a hosted product. This file describes how to report security issues and what scope is in.

## Reporting a vulnerability

Email **lorienslibrary@gmail.com** with subject line starting with `[SECURITY]`. Please include:

- A description of the issue and the failure mode
- Steps to reproduce against the public code (or a minimal pointer if a working PoC would itself be sensitive)
- The commit SHA you reproduced against
- Your name or handle if you'd like attribution; otherwise the disclosure stays anonymous

**Coordinated disclosure window:** 90 days from acknowledgement, by default. Earlier disclosure if a fix lands sooner; longer if the issue requires a coordinated change with downstream deployments. The window is negotiable for cases where someone has already deployed the multi-participant infrastructure (see [DATA_HANDLING.md](DATA_HANDLING.md)).

Please **do not** open public GitHub issues for security reports, and please **do not** include personal data or third-party PII in the report. CAMA is a memory-handling system; a security report should not itself create a memory-handling problem.

## Supported versions

This repository follows rolling-main development. The latest commit on `main` is the supported version. There is no SemVer commitment yet; tagged releases (when they exist) are checkpoints, not LTS branches.

| Version | Supported |
|---|---|
| `main` (latest commit) | ✅ |
| Tagged releases | best-effort only; report against `main` and we'll backport if practical |

## In scope

Issues we care about most:

- **Cross-vault data leakage** — any code path in the multi-tenant stack (`cama_dyad`, `cama_hive_protocol`, `cama_hive_consult`, `cama_hive_resources`, `cama_quad`, `cama_surface`) where one dyad's data becomes reachable from another dyad's runtime, regardless of consent state.
- **Auth bypass** in `cama_hive_api.py` — particularly in the strict-auth path when `CAMA_HIVE_STRICT_AUTH=true`. Defaults are permissive on purpose for single-user local use; the strict path is the deployment-grade boundary.
- **Hive ledger content leakage** — any path where raw user content, names, dyad IDs, or non-rotating identifiers reach `~/.cama-hive/ledger.db`. The structural claim is that only bucketed affect / rotating HMAC signatures / abstracted topic categories cross the boundary; counterexamples are bugs.
- **K-anonymity bypass** in `cama_hive_protocol.query_policies` or `cama_hive_consult.query_peer_experience`. The intended invariant is that aggregate slices are not returned below the configured threshold.
- **PII guard bypass** in `cama_hive_consult` — payloads passing the guard that nonetheless contain emails, phone numbers, names, or other identifiers we did not anticipate.
- **Identity-pin bypass** in `cama_persona_train.run_training` — the trainer should refuse to train when no core identity teachings exist, to prevent pure mirroring. A code path that lets training proceed without pins is in scope.
- **Real-delete regression** — `cama_dyad.delete_dyad`, `cama_persona.delete_adapter`, `cama_surface.delete_memory`, `cama_quad.revoke_handoff` are documented as performing real cascade deletes. Cases where deletion leaves recoverable state behind are in scope.
- **Consent gate bypass** — any operation that should require a `consent.*` flag and proceeds without one.

## Out of scope

These are not security issues for this repository, even if they're worth fixing:

- **Performance issues** — slow retrieval, large memory footprint, embedding-model warm-up time. Open a regular issue.
- **Research-quality questions** — whether the counterweight mechanism "works" therapeutically, whether the safety benchmark numbers are calibrated correctly, whether the affect taxonomy is the right one. These are discussed in the [Zenodo preprint series](https://orcid.org/0009-0005-5803-8401), not as security issues.
- **The single-participant corpus** — the operator's own `~/.cama/memory.db` is not in scope because the repository does not contain it. Personal-name references in old eval files are tracked in [`README.md`](README.md#stability-and-reproducibility-boundary)'s stability-and-reproducibility boundary and are intentionally gitignored.
- **Third-party dependencies** — vulnerabilities in `anthropic`, `peft`, `transformers`, `pytest`, etc. should be reported upstream. We'll bump versions when there's a fixed release.
- **GitHub Actions workflow vulnerabilities** — open against the workflow file, not as a security report against CAMA's safety claims.
- **Default-permissive single-user behavior** — the single-user defaults (no auth, `*` CORS, no FDE requirement) are documented and intentional. Issues that depend on the user not configuring the strict-mode env vars in a multi-user context are deployment issues, not vulnerabilities. The [DATA_HANDLING.md](DATA_HANDLING.md) document names the required hardening; a report should describe how the strict path fails despite correct configuration.

## Threat model boundary

In short: **the threat model assumes the operator controls the host machine.** For single-user local deployment, that boundary is sufficient. For multi-user / study deployment, the operator must additionally configure:

- Full-disk encryption on the host (FileVault / BitLocker / LUKS)
- `CAMA_HIVE_STRICT_AUTH=true` if exposing the hive API beyond localhost
- `CAMA_HIVE_CORS_ORIGINS` restricted to known origins
- `CAMA_PARTICIPANT_ID` for each participant's isolated database

Issues that require breaking one of those documented operator obligations are deployment misconfiguration, not vulnerabilities. Issues that bypass them when correctly set are vulnerabilities.

## Acknowledgements

Security reports that lead to a landed fix will be acknowledged here (with the reporter's preferred name or handle, or anonymously by request).

*(Empty for now — this is an honest baseline, not a list of past disclosures.)*
