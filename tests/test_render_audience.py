import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _render_repo(tmp_path: Path, sidecar_name: str, sidecar: dict) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src" / "genie").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "render.sh", tmp_path / "scripts" / "render.sh")
    shutil.copy2(ROOT / "scripts" / "pre_render.py", tmp_path / "scripts" / "pre_render.py")
    shutil.copy2(
        ROOT / "tests" / "fixtures" / "receivables.serialized_space.json",
        tmp_path / "src" / "genie" / "receivables.serialized_space.json",
    )
    (tmp_path / "src" / "genie" / "receivables.title").write_text(
        "Recebíveis\n", encoding="utf-8"
    )
    (tmp_path / "src" / "genie" / sidecar_name).write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    env = {**os.environ, "FROM_ENV": "dev", "DOMAIN": "recebiveis"}
    subprocess.run(
        ["bash", "scripts/render.sh", "prod"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return tmp_path / "build" / "genie"


def test_render_materializes_canonical_audience_and_build_only_compatibility_copy(tmp_path):
    payload = {"principals": [{"principal": "finance-users", "is_group": True}]}
    built = _render_repo(tmp_path, "receivables.audience.json", payload)

    assert json.loads((built / "receivables.audience.json").read_text()) == payload
    assert json.loads((built / "receivables.access.json").read_text()) == payload
    assert not (tmp_path / "src" / "genie" / "receivables.access.json").exists()


def test_render_keeps_legacy_sidecar_read_only_until_content_repo_switch(tmp_path):
    legacy = {
        "space_permissions": [
            {"principal": "finance-users", "is_group": True, "level": "CAN_RUN"}
        ],
        "uc_principals": [{"principal": "discarded@example.com", "is_group": False}],
    }
    built = _render_repo(tmp_path, "receivables.access.json", legacy)

    assert json.loads((built / "receivables.access.json").read_text()) == legacy
    assert not (built / "receivables.audience.json").exists()
