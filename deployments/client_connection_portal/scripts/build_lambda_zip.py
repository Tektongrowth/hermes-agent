from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


_PACKAGE_FILES = (
    "deployments/__init__.py",
    "deployments/client_connection_portal/__init__.py",
    "deployments/client_connection_portal/lambda_function.py",
    "deployments/client_connection_portal/portal/__init__.py",
    "deployments/client_connection_portal/portal/app.py",
    "deployments/client_connection_portal/portal/dynamo_store.py",
    "deployments/client_connection_portal/portal/lambda_adapter.py",
    "deployments/client_connection_portal/portal/oauth.py",
    "deployments/client_connection_portal/portal/registry.py",
    "deployments/client_connection_portal/portal/security.py",
    "deployments/client_connection_portal/portal/store.py",
)
_FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def build_lambda_zip(*, repo_root: Path, output: Path) -> None:
    repo_root = repo_root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative_name in _PACKAGE_FILES:
            source = repo_root / relative_name
            if not source.is_file():
                raise FileNotFoundError(source)
            info = zipfile.ZipInfo(relative_name, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the test portal Lambda package")
    parser.add_argument("output", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    build_lambda_zip(repo_root=args.repo_root, output=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
