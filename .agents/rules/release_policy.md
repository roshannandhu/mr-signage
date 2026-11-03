# MR Signage Beta Release Policy

## Rule Statement
All application releases, builds, artifacts, and published tags must strictly remain in **Beta** status until explicit, written authorization from the project owner.

## Constraints
1. **Target Horizon:**
   - Production / Play Store deployment is scheduled for at least 6 months into the future.
   - During this minimum 6-month pre-production period, no release may be designated or promoted as production general availability.

2. **Versioning Scheme:**
   - **Android TV Player:** Must follow semantic beta naming `1.0.0-beta.x` with an incrementing integer `versionCode` (starting at `42`).
   - **Frontend Console:** Must follow semantic beta naming `0.1.0-beta.x`.
   - **Backend API:** Must follow semantic beta naming `2.0.0-beta.x`.
   - **Git Tags:** Must use the prefix `v1.0.0-beta.x`.

3. **Enforcement:**
   - No tool, workflow, or automated agent may strip the `-beta` suffix or tag a non-beta release without direct user instruction.
