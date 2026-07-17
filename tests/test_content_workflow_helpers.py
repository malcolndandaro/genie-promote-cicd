from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import changed_space_slugs  # noqa: E402
import content_revision  # noqa: E402


def test_changed_slugs_covers_every_space_contract_sidecar():
    paths = [
        "src/genie/a.serialized_space.json", "src/genie/b.title",
        "src/genie/c.mapping.json", "src/genie/d.audience.json",
        "src/genie/e.revision.json", "src/genie/f.mapping.json",
        "src/dashboards/nope.lvdash.json", "docs/readme.md",
    ]
    assert changed_space_slugs.changed_slugs(paths) == ["a", "b", "c", "d", "e", "f"]


def test_content_tree_revision_is_stable_and_excludes_self_describing_manifest(tmp_path):
    (tmp_path / "src" / "genie").mkdir(parents=True)
    (tmp_path / "src" / "genie" / "x.title").write_text("X")
    first = content_revision.compute_content_tree_revision(tmp_path)
    (tmp_path / "src" / "genie" / "x.revision.json").write_text('{"self":"changes"}')
    assert content_revision.compute_content_tree_revision(tmp_path) == first
    (tmp_path / "src" / "genie" / "x.title").write_text("Y")
    assert content_revision.compute_content_tree_revision(tmp_path) != first
