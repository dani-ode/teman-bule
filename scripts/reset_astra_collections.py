"""Reset seluruh Astra DB vector collections untuk environment development.

Menghapus (drop) semua koleksi fisik yang terdaftar di environment .env,
lalu membuat ulang (createCollection) dengan konfigurasi vector yang sama.
Opsional: --wipe-registry untuk membersihkan vector_collection_registry SQL.

Run from repository root: .venv/bin/python scripts/reset_astra_collections.py [--apply] [--wipe-registry]
Credentials stay in memory; vendor bodies and database error details are not logged.
"""

# SQL identifiers are fixed in this script; values use escaped SQL literals.
# Docker is a trusted local CLI, invoked with argv and never through a shell.
# ruff: noqa: S608, S603, S607, E501

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time

import httpx
from dotenv import dotenv_values

SCOPES = ("user_memory", "learning_content", "toefl_feedback", "agent_knowledge", "podcast_sources")


def identifier(value: str) -> str:
    """Deterministic valid ULID-shaped identifier (128-bit digest)."""
    import hashlib

    number = int.from_bytes(hashlib.sha256(value.encode()).digest()[:16], "big")
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    result = ""
    for _ in range(26):
        result = alphabet[number & 31] + result
        number >>= 5
    return result


def literal(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def insert(table: str, values: dict[str, object], conflict: str) -> str:
    columns = ",".join(values)
    payload = ",".join(literal(v) for v in values.values())
    predicate = " AND ".join(f"{k}={literal(v)}" for k, v in values.items() if k != "id")
    return (
        f"INSERT INTO {table} ({columns}) VALUES ({payload}) "
        f"ON CONFLICT ({conflict}) DO NOTHING;\n"
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM {table} WHERE {predicate}) "
        f"THEN RAISE EXCEPTION 'Seed conflict: {table}'; END IF; END $$;\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Eksekusi drop/create di Astra")
    parser.add_argument(
        "--wipe-registry",
        action="store_true",
        help="Hapus semua baris vector_collection_registry untuk environment development",
    )
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Jangan seed ulang registry setelah wipe",
    )
    args = parser.parse_args()

    env = dotenv_values(".env")
    if env.get("APP_ENV") != "development":
        raise SystemExit("This reset targets development only.")
    required = ("ASTRA_DB_API_ENDPOINT", "ASTRA_DB_APPLICATION_TOKEN")
    if any(not env.get(k) for k in required):
        raise SystemExit("Missing Astra configuration: " + ", ".join(required))
    namespace = env.get("ASTRA_DB_NAMESPACE") or "default_keyspace"
    endpoint = str(env["ASTRA_DB_API_ENDPOINT"]).rstrip("/")
    if not endpoint.startswith("https://"):
        raise SystemExit("Astra endpoint must use HTTPS")

    # Koleksi fisik + dimensi vector yang akan di-reset
    collections: list[tuple[str, int]] = []
    for provider, dimension in (("gemini", 3072), ("openai", 1536)):
        for scope in SCOPES:
            name = env.get(f"ASTRA_COLLECTION_{scope.upper()}_{provider.upper()}")
            if not name or not re.fullmatch(r"dev_temanbule_[a-z0-9_]+", name):
                raise SystemExit("Missing or non-development collection binding: " + scope)
            collections.append((name, dimension))

    print(f"Target: {len(collections)} collections di namespace {namespace}")
    for name, dim in collections:
        print(f"  - {name} (dimension={dim})")

    if not args.apply:
        print("\nDry run only. Gunakan --apply untuk eksekusi.")
        return

    # --- Astra DB: drop & recreate ---
    with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
        url = f"{endpoint}/api/json/v1/{namespace}"
        headers = {"Token": str(env["ASTRA_DB_APPLICATION_TOKEN"])}

        def command(payload: dict, retries: int = 3) -> dict:
            for attempt in range(retries):
                try:
                    response = client.post(url, headers=headers, json=payload, timeout=60)
                    if response.status_code != 200:
                        raise RuntimeError(f"Astra HTTP {response.status_code}")
                    body = response.json()
                    if body.get("errors"):
                        raise RuntimeError("Astra command rejected: " + json.dumps(body["errors"]))
                    return body
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt == retries - 1:
                        raise RuntimeError(f"Astra transport failed after {retries} attempts") from exc
                    print(f"  Retry {attempt + 1}/{retries} setelah timeout...")
                    import time
                    time.sleep(2)
            raise RuntimeError("Unreachable")

        # List existing collections
        body = command({"findCollections": {"options": {"explain": True}}})
        existing = {item["name"]: item for item in body["status"]["collections"]}

        # Drop semua koleksi target
        for name, _ in collections:
            if name in existing:
                command({"deleteCollection": {"name": name}})
                print("Dropped Astra collection:", name)
            else:
                print("Collection tidak ada, skip drop:", name)

        # Create ulang dengan konfigurasi yang sama
        for name, dimension in collections:
            command(
                {
                    "createCollection": {
                        "name": name,
                        "options": {
                            "vector": {"dimension": dimension, "metric": "cosine"},
                            "indexing": {"deny": ["text"]},
                        },
                    }
                }
            )
            print("Created Astra collection:", name)
            # Astra DB butuh waktu initialize schema; Langflow VectorStore
            # bisa gagal infer content_field bila koleksi belum ready.
            time.sleep(2)

        # Verifikasi semua koleksi ready
        print("\nVerifikasi koleksi...")
        body = command({"findCollections": {"options": {"explain": True}}})
        verified = {item["name"] for item in body["status"]["collections"]}
        for name, _ in collections:
            if name not in verified:
                raise RuntimeError("Collection tidak terverifikasi: " + name)
        print("Semua koleksi terverifikasi ready.")

    # --- PostgreSQL: wipe & seed registry ---
    if args.wipe_registry:
        sql = "BEGIN;\n"
        sql += "DELETE FROM vector_collection_registry WHERE environment='development';\n"
        sql += "COMMIT;\n"
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "--env-file",
                ".env.deploy",
                "exec",
                "-T",
                "postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                str(env.get("POSTGRES_USER") or "temanbule"),
                "-d",
                str(env.get("POSTGRES_DB") or "temanbule"),
            ],
            input=sql,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("PostgreSQL wipe failed")
        print("PostgreSQL: vector_collection_registry dihapus untuk environment development")

        if not args.skip_seed:
            # Seed ulang registry dengan status active
            sql = "BEGIN; SELECT pg_advisory_xact_lock(20260928);\n"
            for provider, dimension, model, document_task, query_task in (
                ("gemini", 3072, "gemini-embedding-001", "RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"),
                ("openai", 1536, "text-embedding-3-small", "search_document", "search_query"),
            ):
                provider_id = identifier("launch:provider:" + provider)
                model_id = identifier("launch:model:" + model)
                profile_id = identifier("launch:profile:" + model)
                for scope in SCOPES:
                    name = env.get(f"ASTRA_COLLECTION_{scope.upper()}_{provider.upper()}")
                    if not name:
                        raise SystemExit("Missing collection binding")
                    sql += insert(
                        "vector_collection_registry",
                        {
                            "id": identifier("launch:collection:" + name),
                            "environment": "development",
                            "scope": scope,
                            "profile_id": profile_id,
                            "physical_name": name,
                            "similarity_metric": "cosine",
                            "metadata_index_policy": '{"deny":["text"]}',
                            "policy_version": "launch-20260928",
                            "status": "active",
                        },
                        "environment,scope,profile_id",
                    )
            sql += "COMMIT;\n"
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    ".env",
                    "--env-file",
                    ".env.deploy",
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    str(env.get("POSTGRES_USER") or "temanbule"),
                    "-d",
                    str(env.get("POSTGRES_DB") or "temanbule"),
                ],
                input=sql,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise RuntimeError("PostgreSQL seed failed")
            print("PostgreSQL: vector_collection_registry di-seed ulang dengan status active")

    print("\nReset selesai.")


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, RuntimeError) as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else "Vendor transport failed")
        raise SystemExit(1) from None
