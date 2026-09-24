"""Bootstrap mechanism (FND-03/DEC-17): manifest validation, preview/apply idempoten."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.reliability.models import BootstrapRun
from temanbule.modules.reliability.repository import ReliabilityRepository
from temanbule.platform.errors import ConflictError, ValidationError
from temanbule.platform.security import new_ulid


@dataclass
class ManifestEntry:
    kind: str
    natural_key: dict[str, Any]
    data: dict[str, Any]


@dataclass
class Manifest:
    manifest_version: str
    environment: str
    entries: list[ManifestEntry] = field(default_factory=list)

    def checksum(self) -> str:
        canonical = json.dumps(
            {
                "manifest_version": self.manifest_version,
                "environment": self.environment,
                "entries": [
                    {"kind": e.kind, "natural_key": e.natural_key, "data": e.data}
                    for e in self.entries
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_manifest(raw: dict[str, Any]) -> Manifest:
    """Validasi struktur manifest; gagal eksplisit bila tidak sesuai."""
    if not isinstance(raw, dict):
        raise ValidationError("Manifest harus berupa object JSON.")
    manifest_version = raw.get("manifest_version")
    environment = raw.get("environment")
    if not isinstance(manifest_version, str) or not manifest_version:
        raise ValidationError("manifest_version wajib diisi.")
    if not isinstance(environment, str) or not environment:
        raise ValidationError("environment wajib diisi.")
    entries_raw = raw.get("entries", [])
    if not isinstance(entries_raw, list):
        raise ValidationError("entries harus berupa array.")
    entries: list[ManifestEntry] = []
    for i, item in enumerate(entries_raw):
        if not isinstance(item, dict):
            raise ValidationError(f"entries[{i}] harus object.")
        kind = item.get("kind")
        natural_key = item.get("natural_key")
        data = item.get("data", {})
        if not isinstance(kind, str) or not kind:
            raise ValidationError(f"entries[{i}].kind wajib diisi.")
        if not isinstance(natural_key, dict) or not natural_key:
            raise ValidationError(f"entries[{i}].natural_key wajib object tidak kosong.")
        if not isinstance(data, dict):
            raise ValidationError(f"entries[{i}].data harus object.")
        entries.append(ManifestEntry(kind=kind, natural_key=natural_key, data=data))
    return Manifest(manifest_version=manifest_version, environment=environment, entries=entries)


class BootstrapService:
    """Preview/apply manifest secara idempoten; checksum conflict ditolak."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ReliabilityRepository(session)

    async def preview(self, manifest: Manifest) -> dict[str, Any]:
        checksum = manifest.checksum()
        runs = await self.repo.get_bootstrap_runs(
            environment=manifest.environment, manifest_version=manifest.manifest_version
        )
        existing_checksum = runs[0].checksum if runs else None
        conflict = existing_checksum is not None and existing_checksum != checksum
        return {
            "manifest_version": manifest.manifest_version,
            "environment": manifest.environment,
            "checksum": checksum,
            "entries_count": len(manifest.entries),
            "already_applied": existing_checksum == checksum,
            "checksum_conflict": conflict,
        }

    async def apply(self, manifest: Manifest, *, actor_ref: str | None = None) -> BootstrapRun:
        checksum = manifest.checksum()
        runs = await self.repo.get_bootstrap_runs(
            environment=manifest.environment, manifest_version=manifest.manifest_version
        )
        if runs and runs[0].checksum != checksum:
            raise ConflictError(
                "Manifest version sama dengan checksum berbeda ditolak.",
                code="BOOTSTRAP_CHECKSUM_CONFLICT",
            )
        if runs:
            # Idempoten: sudah pernah di-apply dengan checksum sama
            return runs[0]

        attempt = await self.repo.get_max_bootstrap_attempt(
            environment=manifest.environment, manifest_version=manifest.manifest_version
        ) + 1
        now = datetime.now(UTC)
        run = BootstrapRun(
            id=new_ulid(),
            environment=manifest.environment,
            manifest_version=manifest.manifest_version,
            checksum=checksum,
            attempt_number=attempt,
            status="succeeded",
            actor_ref=actor_ref,
            started_at=now,
            ended_at=now,
            change_summary=json.dumps(
                {
                    "entries": len(manifest.entries),
                    "kinds": sorted({e.kind for e in manifest.entries}),
                },
                sort_keys=True,
            ),
        )
        self.repo.add_bootstrap_run(run)
        # Entry apply per-kind ditambahkan pada slice pemiliknya (Phase 2+).
        return run
