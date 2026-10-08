"""One-off: hapus projection dokumen Langflow dari koleksi Astra agent_knowledge.

Kenapa script ini ada: dokumen agent_knowledge saat ini ditulis oleh Langflow
dengan custom field di bawah objek ``metadata`` (metadata.document_id,
metadata.agent_id, metadata.chunk_id). Endpoint internal delete (sebelum
perbaikan _expand_filter) mengirim filter top-level sehingga tidak pernah
match. Script ini memakai AstraAdminClient yang sudah diperbaiki untuk
menghapus satu document_id dari semua koleksi agent_knowledge aktif.

Jalankan dari repo root:

    .venv/bin/python scripts/delete_agent_knowledge_doc.py \
        --document-id 01M4DKCR33XVMX4WTKHC82AM36

Tambahkan --dry-run untuk hanya menghitung kandidat tanpa menghapus.
Credentials dibaca dari .env; tidak pernah dilog.
"""

from __future__ import annotations

import argparse
import asyncio
import re

import httpx
from dotenv import dotenv_values

from temanbule.modules.knowledge.astra_admin import (
    AstraAdminClient,
    AstraAdminConfig,
)

# Koleksi fisik agent_knowledge untuk environment development (dari
# vector_collection_registry). Hardcode aman: one-off script, dev-only.
COLLECTIONS = (
    "dev_temanbule_agent_knowledge_gemini_v1",
    "dev_temanbule_agent_knowledge_openai_v1",
)

_ULID = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")


async def _count_matches(
    client: AstraAdminClient, collection: str, document_id: str
) -> int:
    """Hitung kandidat via find (limit 0 tidak ada; pakai pageState kecil)."""
    config = client._config  # one-off script: akses config trusted internal
    base = config.astra_api_endpoint.rstrip("/")
    url = f"{base}/api/json/v1/{config.astra_namespace}/{collection}"
    body = await client._request_json(
        url=url,
        json_body={
            "find": {
                "filter": {
                    "$or": [
                        {"document_id": document_id},
                        {"metadata.document_id": document_id},
                    ]
                },
                "options": {"limit": 1},
            }
        },
        failure_label="Astra find",
    )
    data = body.get("data") or {}
    docs = data.get("documents") or []
    return len(docs)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not _ULID.fullmatch(args.document_id):
        raise SystemExit("document-id bukan ULID valid.")

    env = dotenv_values(".env")
    endpoint = (env.get("ASTRA_DB_API_ENDPOINT") or "").rstrip("/")
    token = env.get("ASTRA_DB_APPLICATION_TOKEN") or ""
    namespace = env.get("ASTRA_DB_NAMESPACE") or "default_keyspace"
    if not endpoint.startswith("https://") or not token.strip():
        raise SystemExit("Missing/invalid Astra configuration in .env")

    client = AstraAdminClient(
        AstraAdminConfig(
            astra_api_endpoint=endpoint,
            astra_application_token=token,
            astra_namespace=namespace,
            request_timeout_seconds=30.0,
        )
    )

    total = 0
    for collection in COLLECTIONS:
        try:
            if args.dry_run:
                matched = await _count_matches(client, collection, args.document_id)
                print(f"[dry-run] {collection}: match (sample<=1) = {matched}")
                continue
            deleted = await client.delete_many(
                collection=collection, filters={"document_id": args.document_id}
            )
        except Exception as exc:  # noqa: BLE001 - one-off script, fail loud per collection
            print(f"ERROR {collection}: {type(exc).__name__}: {exc}")
            continue
        print(f"{collection}: deleted_count = {deleted}")
        total += max(deleted, 0)
    if not args.dry_run:
        print(f"TOTAL deleted = {total}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (httpx.HTTPError, RuntimeError) as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else "Vendor transport failed")
        raise SystemExit(1) from None
