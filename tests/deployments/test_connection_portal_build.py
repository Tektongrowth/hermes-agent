from __future__ import annotations

import zipfile
from pathlib import Path

from deployments.client_connection_portal.scripts.build_lambda_zip import build_lambda_zip


ROOT = Path(__file__).resolve().parents[2]


def test_lambda_package_contains_only_required_python_sources(tmp_path: Path) -> None:
    output = tmp_path / "portal.zip"

    build_lambda_zip(repo_root=ROOT, output=output)

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    assert "deployments/__init__.py" in names
    assert "deployments/client_connection_portal/lambda_function.py" in names
    assert "deployments/client_connection_portal/portal/app.py" in names
    assert "deployments/client_connection_portal/portal/dynamo_store.py" in names
    assert not any("__pycache__" in name for name in names)
    assert not any(name.endswith(".pyc") for name in names)
    assert not any("test" in Path(name).name for name in names)
    assert "deployments/client_connection_portal/DESIGN.md" not in names
    assert "deployments/client_connection_portal/admin.py" not in names
