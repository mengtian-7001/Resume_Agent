"""Create a deterministic, importable backend bundle for direct Vercel uploads."""

from __future__ import annotations

import base64
import hashlib
import io
import textwrap
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_APP = ROOT / "backend" / "app"
OUTPUT = ROOT / "api" / "backend_bundle.py"


def main() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        package_info = zipfile.ZipInfo("backend/__init__.py")
        package_info.date_time = (2020, 1, 1, 0, 0, 0)
        package_info.compress_type = zipfile.ZIP_DEFLATED
        package_info.external_attr = 0o644 << 16
        archive.writestr(package_info, b'"""Bundled Resume Agent backend."""\n')
        for source in sorted(BACKEND_APP.glob("*.py")):
            info = zipfile.ZipInfo(f"backend/app/{source.name}")
            info.date_time = (2020, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())

    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    encoded = base64.b64encode(payload).decode("ascii")
    wrapped = "\n".join(f'    "{line}"' for line in textwrap.wrap(encoded, 100))
    generated = "\n".join(
        [
            '"""Generated backend bundle. Do not edit or commit this file."""',
            "",
            "import base64",
            "import hashlib",
            "import sys",
            "from pathlib import Path",
            "",
            f'BUNDLE_SHA256 = "{digest}"',
            "BUNDLE_B64 = (",
            wrapped,
            ")",
            "",
            "def install_backend_bundle() -> None:",
            '    target = Path("/tmp") / f"resume-agent-backend-{BUNDLE_SHA256}.zip"',
            "    if not target.exists():",
            "        payload = base64.b64decode(BUNDLE_B64)",
            "        if hashlib.sha256(payload).hexdigest() != BUNDLE_SHA256:",
            '            raise RuntimeError("backend bundle checksum mismatch")',
            "        target.write_bytes(payload)",
            "    path = str(target)",
            "    if path not in sys.path:",
            "        sys.path.insert(0, path)",
            "",
        ]
    )
    OUTPUT.write_text(
        generated,
        encoding="utf-8",
    )
    print(f"created {OUTPUT.relative_to(ROOT)} ({len(payload)} bytes, sha256={digest[:12]}…)")


if __name__ == "__main__":
    main()
