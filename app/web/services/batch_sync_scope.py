"""Bind a local batch schedule to the network that authorized its loopback source."""

from hashlib import sha256
from pathlib import Path
from uuid import UUID

_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
_NETWORK_NS = Path("/proc/self/ns/net")


def local_batch_sync_scope() -> str | None:
    """Identify a Linux boot and network namespace without disclosing either identifier."""
    try:
        boot_id = UUID(_BOOT_ID.read_text(encoding="ascii").strip())
        namespace = _NETWORK_NS.stat()
    except (OSError, ValueError):
        # No unverifiable fallback: an identical 127.0.0.1 in another process
        # can denote a completely different source database.
        return None
    material = f"batch-sync-scope-v1:{boot_id.hex}:{namespace.st_dev}:{namespace.st_ino}"
    return sha256(material.encode("ascii")).hexdigest()
