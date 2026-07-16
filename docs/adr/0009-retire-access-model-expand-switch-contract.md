# Retire the demo access model through an expand-switch-contract cutover

**Status:** accepted (2026-07-16)

The pilot replaces the demo-era AccessSpec, Access Request, Approver, multi-level Genie ACL and UC
grant model with required AudienceSpec (`CAN_RUN` only) and validation-only `AUDIENCE-01`. Because
the content repo executes engine code and the sidecar spans both repositories, the cutover expands
the engine first, atomically switches pinned content workflows/sidecars, and only then contracts the
legacy reader and Lakebase schema. The compatibility window is read-only, signal-based and ends
before the pilot; it never permits UC mutation or legacy writes. This keeps the live deploy path
available during the cross-repo switch without turning obsolete behavior into a supported mode.

The active app removes Access Requests, the Approver role and environment inventory in Phase 1.
Phase 2 deletes the narrow legacy translator and disposable demo schema/data. Technical
`CAN_MANAGE`, Steward gates, promotion/rehydrate audit, and CI-SP UC grant inspection remain because
they serve the pilot's runtime and validation boundaries rather than the retired access product.
