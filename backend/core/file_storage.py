"""SCRUM-46: secure on-disk storage for uploaded prescription originals.

Guarantees
----------
* Files are stored OUTSIDE any publicly served/static directory
  (``<project>/secure_uploads``, never under ``frontend/`` or ``static/``), so
  they are never directly reachable over HTTP. Access is only ever granted
  through an authenticated, ownership-checked endpoint.
* The on-disk filename is a random UUID with a server-controlled extension; the
  original filename is kept in the DB only for display and is NEVER used to
  build a path (no path traversal / no user-controlled path components).
* The stored extension is derived from the SNIFFED content type, not from the
  client-supplied name or declared MIME type, so a mislabelled upload cannot
  masquerade as a different type.

Validation (fail closed)
------------------------
* ``sniff_mime`` inspects magic bytes and only recognises application/pdf and
  image/png|jpeg|gif|webp. Anything else is rejected.
* ``ALLOWED_EXTENSIONS`` maps each recognised MIME to a fixed extension.
* ``MAX_UPLOAD_BYTES`` caps size; oversized or unreadable content is rejected.

The DB persists only the relative reference (``<owner>/<uuid>.<ext>``) plus the
display name, MIME type and byte size — never the bytes themselves.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

# Only these original types are accepted; extension is derived from these.
ALLOWED_MIME_TO_EXT = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}

# 5 MB ceiling for a single prescription scan/attachment.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class UploadValidationError(Exception):
    """Raised when an upload fails type/size validation. Carries a safe message."""


@dataclass(frozen=True)
class StoredFile:
    """Result of a successful, validated store. ``relative_path`` is DB-safe."""

    relative_path: str
    file_name: str
    mime_type: str
    size_bytes: int


# Secure root: <project>/secure_uploads. Deliberately NOT under frontend/ or any
# static dir. Created lazily with owner-only-ish permissions (0700) on POSIX.
_UPLOAD_ROOT = (
    Path(__file__).resolve().parents[2] / "secure_uploads"
)


def upload_root() -> Path:
    """Return (and lazily create) the secure upload root directory."""
    root = _UPLOAD_ROOT
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        # Best-effort on filesystems that do not support POSIX modes (Windows).
        pass
    return root


def sniff_mime(content: bytes) -> Optional[str]:
    """Return a recognised MIME type from magic bytes, else ``None``."""
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    # WebP: "RIFF" .... "WEBP"
    if len(content) >= 12 and content[0:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _safe_owner_dir(owner_key: str) -> Path:
    """Build ``<root>/<owner>`` where owner is strictly an integer id string."""
    if not re.fullmatch(r"\d{1,10}", owner_key or ""):
        raise UploadValidationError("Invalid storage owner.")
    return upload_root() / owner_key


def validate_and_store(
    owner_key: str, original_filename: str, content: bytes
) -> StoredFile:
    """Validate ``content`` and store it securely under the owner's directory.

    Raises ``UploadValidationError`` with a safe, user-facing message on any
    failure (empty, oversized, or unrecognised type). Never returns a path that
    incorporates the original filename.
    """
    if not content:
        raise UploadValidationError("The uploaded file is empty.")

    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadValidationError(
            "The file is too large. Maximum size is 5 MB."
        )

    mime = sniff_mime(content)
    if mime is None or mime not in ALLOWED_MIME_TO_EXT:
        raise UploadValidationError(
            "Unsupported file type. Allowed: PDF, PNG, JPG, GIF or WEBP."
        )

    ext = ALLOWED_MIME_TO_EXT[mime]
    owner_dir = _safe_owner_dir(owner_key)
    owner_dir.mkdir(parents=True, exist_ok=True)

    random_name = f"{uuid.uuid4().hex}.{ext}"
    absolute = owner_dir / random_name
    with open(absolute, "wb") as fh:
        fh.write(content)

    # DB stores only "<owner>/<random>.<ext>" — no traversal, no original name.
    relative_path = f"{owner_key}/{random_name}"
    display_name = _display_name(original_filename)

    return StoredFile(
        relative_path=relative_path,
        file_name=display_name,
        mime_type=mime,
        size_bytes=len(content),
    )


def _display_name(original_filename: str | None) -> str:
    """Sanitise the client filename for DISPLAY only (never used as a path)."""
    if not original_filename:
        return "prescription"
    # Keep only the final path segment and strip control chars.
    base = original_filename.replace("\\", "/").split("/")[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    if not base or base in {".", ".."}:
        return "prescription"
    return base[:255]


def delete_file(relative_path: str) -> None:
    """Best-effort removal of a stored file by its DB reference.

    Used to avoid orphaned files when a later step (e.g. the DB insert) fails
    after the bytes have already been written. Confinement-safe: only paths that
    resolve inside the upload root are removed.
    """
    try:
        candidate = resolve_path(relative_path)
    except UploadValidationError:
        return
    try:
        if candidate.is_file():
            candidate.unlink()
    except OSError:
        pass


def resolve_path(relative_path: str) -> Path:
    """Resolve a stored ``relative_path`` to an absolute path, confinement-safe.

    The resolved path is verified to stay within the secure upload root so a
    malicious/​corrupted DB value can never escape the upload directory.
    """
    root = upload_root().resolve()
    candidate = (root / relative_path).resolve()
    if not str(candidate).startswith(str(root) + os.sep):
        raise UploadValidationError("Invalid file reference.")
    return candidate
