"""resource_kind — the ONE registry of per-resource-kind facts (the promotion pipeline's kind seam).

The promotion pipeline (export -> review -> gate -> draft PR -> merge -> gated deploy) is
resource-agnostic by design. Everything that genuinely VARIES between a Genie Space and an AI/BI
dashboard is a value in this module, so no other module branches on kind inline.

This is deliberately a frozen data registry with NO I/O, no SDK import and no behaviour: it is as
pure and offline-testable as `audience_spec` / `change_request`, and it is imported by BOTH the app
(`app/app_logic.py`) and the CI/deploy scripts (`scripts/render.sh` helpers,
`scripts/deploy_attempt.py`) the same way those modules already are.

The two SDK-touching halves live elsewhere on purpose:
  - the workspace adapter (list/get/create/update/resolve-by-title) — `workspace_resource.py`;
  - the content adapter (which text carries table refs; how to build a review context) —
    `pre_render.py` / `review_core.py` / `dashboard_check.py`.

`GENIE_SPACE` values are byte-identical to the constants they replace, so registering this seam
changes NO Genie behaviour — that invariant is the acceptance bar for the whole slice.
"""
from __future__ import annotations

import dataclasses

# The canonical kind strings. These are the SAME values the SPA's kind registry
# (`web/src/lib/resources.ts`) and the persisted `promotions.resource_kind` column already use —
# do NOT invent `aibi_dashboard` / `lakeview` variants.
GENIE_SPACE = "genie_space"
DASHBOARD = "dashboard"


@dataclasses.dataclass(frozen=True)
class ResourceKind:
    """Every per-kind fact the pipeline needs, in one place.

    Field-by-field rationale for the ones that are easy to get wrong:

    ``permissions_object_type`` — the `/api/2.0/permissions/{type}/{id}` segment. `genie` and
    `dashboards` are SIBLINGS in that namespace (both verified live). NOTE the dashboard value is
    PLURAL: the singular `dashboard` is rejected by the API, and `dbsql-dashboards` is a DIFFERENT
    object type (legacy Redash) that this accelerator does not promote.

    ``tag_entity_type`` — the workspace-entity-tag segment for the governed
    `system.certification_status` tag. `geniespaces` and `dashboards` are the only accepted spellings
    (verified live: `dashboard`, `lakeviewdashboards` and `aibidashboards` all raise
    "Entity type is not supported for tag operations"). ADR-0007 explicitly warns that a generic DABs
    example is NOT evidence for a resource's tag/ACL support, which is why each value here was probed
    against a live workspace rather than inferred.

    ``audience_level`` — the ONE level the app manages for a declared audience principal. Genie
    derives `CAN_RUN`; a dashboard derives `CAN_READ`, the least-privilege level that lets a business
    user open a published dashboard. Because dashboards are published with `embed_credentials=false`,
    the viewer's own Unity Catalog SELECT and warehouse access still apply — so, exactly as for
    Genie, promotion grants VISIBILITY and never DATA.

    ``has_benchmarks`` — whether the kind carries eval benchmark questions (`benchmarks.questions`).
    False for dashboards, which is what suppresses EVAL-01/EVAL-RUN for them rather than letting
    those rules fire on an artifact that can never satisfy them.

    ``sql_only_ref_scan`` — whether the deterministic catalog allowlist scans only the kind's
    data-carrying SQL instead of the whole serialized document. True for dashboards: a dashboard's
    only data-access path is `datasets[].queryLines`, whereas its markdown/text widgets are prose. A
    whole-document scan there produces FALSE positives (a real dev dashboard's markdown link made the
    3-part-ref regex match `en.wikipedia.org`) — see `pre_render.dashboard_sql_text`.
    """

    kind: str
    src_dir: str
    build_subdir: str
    artifact_suffix: str
    slug_prefix: str
    bundle_collection: str
    permissions_object_type: str
    tag_entity_type: str
    audience_level: str
    has_benchmarks: bool
    sql_only_ref_scan: bool
    label_pt: str
    # Whether an id that ALREADY starts with a letter may be used as a slug unprefixed. True only
    # for Genie, where it preserves the pre-seam `space_slug` behaviour byte-for-byte. Dashboards
    # always prefix, which is what guarantees the two kinds' slug namespaces stay disjoint even for
    # an id shape that happens to start with a letter.
    bare_slug_if_alpha: bool

    # --- committed sidecar paths (the content repo's per-resource contract) -------------------
    #
    # One method per sidecar so no caller ever concatenates a path by hand. The `.title` sidecar is
    # REQUIRED for both kinds: it is how the deploy resolves the live resource id (neither
    # `bundle summary` nor the DABs deploy reports it back), so a missing/blank title fails render
    # rather than deploying something unresolvable.

    def artifact_path(self, slug: str) -> str:
        """The promoted definition itself (serialized_space JSON / .lvdash.json)."""
        return f"{self.src_dir}/{slug}{self.artifact_suffix}"

    def title_path(self, slug: str) -> str:
        """The declared production display name — also the deploy's id-resolution key."""
        return f"{self.src_dir}/{slug}.title"

    def audience_path(self, slug: str) -> str:
        """The required canonical AudienceSpec sidecar (ADR-0009)."""
        return f"{self.src_dir}/{slug}.audience.json"

    def mapping_path(self, slug: str) -> str:
        """The optional table de-para, applied by CI AFTER the rebind and BEFORE the allowlist."""
        return f"{self.src_dir}/{slug}.mapping.json"

    def revision_path(self, slug: str) -> str:
        """The immutable content/engine revision pair reviewed and deployed (ADR-0008)."""
        return f"{self.src_dir}/{slug}.revision.json"

    def slug_for(self, resource_id: str, pinned: "dict[str, str] | None" = None) -> str:
        """A stable, branch/path/DABs-identifier-safe slug for one resource.

        A pinned slug wins (so an already-promoted resource keeps its committed file, branch and
        generated bundle resource); otherwise the id is sanitized to alnum/underscore and given this
        kind's prefix. The prefix is what keeps the two kinds' slug namespaces DISJOINT, so a
        dashboard slug can never be mistaken for a Space slug by the CI diff, and
        `deployment_attempts.target_ids` can hold both kinds keyed by slug with no collision.
        """
        if pinned:
            existing = pinned.get(resource_id)
            if existing:
                return existing
        safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in resource_id)
        # A leading letter is required for a DABs resource key / git branch segment.
        if self.bare_slug_if_alpha and safe[:1].isalpha():
            return safe
        return f"{self.slug_prefix}{safe}"


GENIE_SPACE_KIND = ResourceKind(
    kind=GENIE_SPACE,
    src_dir="src/genie",
    build_subdir="genie",
    artifact_suffix=".serialized_space.json",
    slug_prefix="s_",
    bundle_collection="genie_spaces",
    permissions_object_type="genie",
    tag_entity_type="geniespaces",
    audience_level="CAN_RUN",
    has_benchmarks=True,
    sql_only_ref_scan=False,
    label_pt="Genie Space",
    bare_slug_if_alpha=True,
)

DASHBOARD_KIND = ResourceKind(
    kind=DASHBOARD,
    src_dir="src/dashboards",
    build_subdir="dashboards",
    artifact_suffix=".lvdash.json",
    slug_prefix="d_",
    bundle_collection="dashboards",
    permissions_object_type="dashboards",
    tag_entity_type="dashboards",
    audience_level="CAN_READ",
    has_benchmarks=False,
    sql_only_ref_scan=True,
    label_pt="Painel AI/BI",
    bare_slug_if_alpha=False,
)

KINDS: "dict[str, ResourceKind]" = {
    GENIE_SPACE: GENIE_SPACE_KIND,
    DASHBOARD: DASHBOARD_KIND,
}

# The default kind for any caller that predates the kind seam (a `/promote` body with no
# `resource_kind`, a stored Promotion row written before dashboards existed). Keeping this explicit
# means "no kind supplied" can never silently mean "no kind" — it means Genie, exactly as before.
DEFAULT_KIND = GENIE_SPACE


def get(kind: "str | None") -> ResourceKind:
    """Resolve a kind string to its registry entry; ``None``/blank falls back to ``DEFAULT_KIND``.

    Raises ``ValueError`` on an unknown kind rather than defaulting — a typo'd kind must fail loudly
    at the boundary, not quietly promote a dashboard through the Genie path (fail closed).
    """
    resolved = (kind or DEFAULT_KIND).strip() or DEFAULT_KIND
    try:
        return KINDS[resolved]
    except KeyError:
        raise ValueError(
            f"unknown resource kind {resolved!r}; expected one of {sorted(KINDS)}") from None


def all_kinds() -> "tuple[ResourceKind, ...]":
    """Every registered kind, in a deterministic order (Genie first — the original kind).

    Used by the render + deploy paths, which must iterate EVERY kind rather than name them, so a
    third kind needs a registry entry and its adapters, not new loops.
    """
    return (GENIE_SPACE_KIND, DASHBOARD_KIND)
