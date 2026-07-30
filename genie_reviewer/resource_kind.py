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
    # Whether this kind files each resource in its OWN directory under a business area
    # (`<area>/<name>/`) with fixed sidecar names, instead of flat `<slug>.<sidecar>` files. See the
    # path-builder block below for the two shapes and why Genie stays flat.
    nested_layout: bool = False

    # --- committed sidecar paths (the content repo's per-resource contract) -------------------
    #
    # One method per sidecar so no caller ever concatenates a path by hand. The `.title` sidecar is
    # REQUIRED for both kinds: it is how the deploy resolves the live resource id (neither
    # `bundle summary` nor the DABs deploy reports it back), so a missing/blank title fails render
    # rather than deploying something unresolvable.

    # --- how a slug maps onto a committed path ------------------------------------------------
    #
    # Two layouts exist, and which one a kind uses is `nested_layout`:
    #
    #   FLAT (Genie, historical):   src/genie/<slug>.serialized_space.json
    #                               src/genie/<slug>.title
    #   NESTED (dashboards):        src/dashboards/<area>/<name>/dashboard.lvdash.json
    #                               src/dashboards/<area>/<name>/title
    #
    # The nested layout files a dashboard under its owning BUSINESS AREA, which is how a business
    # author looks for it, and gives each resource its own directory so its sidecars sit together
    # instead of being interleaved with every other resource's in one flat listing. There is no
    # version directory: git already holds the history, so a new revision REPLACES the content of the
    # same directory and the diff shows what changed.
    #
    # Genie stays flat deliberately — 7 Spaces are already committed and promoted that way, and moving
    # them would rewrite live promotion branches for no user-visible gain.

    def _base(self, slug: str) -> str:
        """The path prefix a resource's files share, and the sidecar naming that follows from it."""
        return f"{self.src_dir}/{slug}"

    def artifact_path(self, slug: str) -> str:
        """The promoted definition itself (serialized_space JSON / .lvdash.json)."""
        if self.nested_layout:
            return f"{self._base(slug)}/dashboard{self.artifact_suffix}"
        return f"{self._base(slug)}{self.artifact_suffix}"

    def title_path(self, slug: str) -> str:
        """The declared production display name — also the deploy's id-resolution key."""
        return f"{self._base(slug)}/title" if self.nested_layout else f"{self._base(slug)}.title"

    def audience_path(self, slug: str) -> str:
        """The required canonical AudienceSpec sidecar (ADR-0009)."""
        if self.nested_layout:
            return f"{self._base(slug)}/audience.json"
        return f"{self._base(slug)}.audience.json"

    def mapping_path(self, slug: str) -> str:
        """The optional table de-para, applied by CI AFTER the rebind and BEFORE the allowlist."""
        if self.nested_layout:
            return f"{self._base(slug)}/mapping.json"
        return f"{self._base(slug)}.mapping.json"

    def revision_path(self, slug: str) -> str:
        """The immutable content/engine revision pair reviewed and deployed (ADR-0008)."""
        if self.nested_layout:
            return f"{self._base(slug)}/revision.json"
        return f"{self._base(slug)}.revision.json"

    def resource_key(self, slug: str) -> str:
        """The DABs resource key for this slug.

        A nested slug carries a `/`, which DABs accepts in a key (probed) but which reads badly in
        `bundle summary` and in the app's deploy panel — so it is flattened to `<area>__<name>`. The
        mapping is total and reversible enough to trace back, and stays disjoint from Genie's keys
        because a Genie slug never contains `__` from this path (its ids are hex).
        """
        return slug.replace("/", "__") if self.nested_layout else slug

    def slug_for(self, resource_id: str, pinned: "dict[str, str] | None" = None, *,
                 area: "str | None" = None, name: "str | None" = None) -> str:
        """A stable, branch/path/DABs-identifier-safe slug for one resource.

        A pinned slug always wins (so an already-promoted resource keeps its committed files, branch
        and generated bundle resource across a layout change).

        For a NESTED kind the slug is `<area>/<name>`, both declared by the author at promotion time:
        the area from the controlled vocabulary (`business_area`) and the name derived from the prod
        title. The slug is therefore MEANINGFUL rather than an opaque id — which is the point of the
        layout, since a human browses the content repo by area.

        For a FLAT kind the id is sanitized to alnum/underscore and given this kind's prefix. That
        prefix keeps the kinds' slug namespaces disjoint so a slug can never be mistaken for the other
        kind's by the CI diff or by `deployment_attempts.target_ids`.
        """
        if pinned:
            existing = pinned.get(resource_id)
            if existing:
                return existing
        if self.nested_layout:
            if not (area and name):
                raise ValueError(
                    f"{self.label_pt} requires a business area and a name to build its slug "
                    "(both are author declarations, not derivable from the resource id)")
            return f"{area}/{name}"
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
    nested_layout=True,
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
