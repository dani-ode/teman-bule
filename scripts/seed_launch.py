"""Provision launch catalog and ten empty Astra vector collections, rerunnable.

Run from repository root: .venv/bin/python scripts/seed_launch.py [--apply]
Credentials stay in memory; vendor bodies and database error details are not logged.
"""

# SQL identifiers are fixed in this script; values use escaped SQL literals.
# Docker is a trusted local CLI, invoked with argv and never through a shell.
# ruff: noqa: S608, S603, S607, E501

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import httpx
from dotenv import dotenv_values

SCOPES = ("user_memory", "learning_content", "toefl_feedback", "agent_knowledge", "podcast_sources")


def identifier(value: str) -> str:
    """Deterministic valid ULID-shaped identifier (128-bit digest)."""
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
    # Fail on drift rather than silently overwrite an existing immutable revision.
    predicate = " AND ".join(f"{k}={literal(v)}" for k, v in values.items() if k != "id")
    return (
        f"INSERT INTO {table} ({columns}) VALUES ({payload}) "
        f"ON CONFLICT ({conflict}) DO NOTHING;\n"
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM {table} WHERE {predicate}) "
        f"THEN RAISE EXCEPTION 'Seed conflict: {table}'; END IF; END $$;\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    env = dotenv_values(".env")
    if env.get("APP_ENV") != "development":
        raise SystemExit("This launch seed targets development only.")
    required = ("ASTRA_DB_API_ENDPOINT", "ASTRA_DB_APPLICATION_TOKEN")
    if any(not env.get(k) for k in required):
        raise SystemExit("Missing Astra configuration: " + ", ".join(required))
    namespace = env.get("ASTRA_DB_NAMESPACE") or "default_keyspace"
    endpoint = str(env["ASTRA_DB_API_ENDPOINT"]).rstrip("/")
    if not endpoint.startswith("https://"):
        raise SystemExit("Astra endpoint must use HTTPS")
    sql = "BEGIN; SELECT pg_advisory_xact_lock(20260928);\n"
    for code in ("vip", "advance"):
        sql += f"INSERT INTO plans(code,status) VALUES ('{code}','active') ON CONFLICT(code) DO NOTHING;\n"
        policy = json.dumps({
            "plan": code, "payer": "wallet" if code == "vip" else "byok",
            "platform_fee_idr": 0, "minimum_topup_idr": 25000 if code == "vip" else 0,
            "idr_per_wallet_unit": 1, "tts_payer": "platform", "embedding_payer": "platform",
            "admin_key_fallback": False,
        }, sort_keys=True)
        sql += insert("plan_policy_versions", {
            "id": identifier("launch:plan:" + code), "plan_code": code,
            "revision": 20260928, "policy": policy, "status": "published",
        }, "plan_code,revision")
        sql += f"UPDATE plans SET policy_version=20260928 WHERE code='{code}' AND (policy_version IS NULL OR policy_version<=20260928);\n"
    for code, amount in (("starter", 25000), ("regular", 50000), ("plus", 100000), ("max", 250000)):
        sql += insert("token_package_versions", {
            "id": identifier("launch:package:" + code), "package_code": "vip_" + code,
            "revision": 1, "currency": "IDR", "amount_minor": amount,
            "token_units": amount, "display_scale": 1, "status": "published",
        }, "package_code,revision")
    collections = []
    for provider, dimension, model, document_task, query_task in (
        ("gemini", 768, "gemini-embedding-001", "RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"),
        ("openai", 1536, "text-embedding-3-small", "search_document", "search_query"),
    ):
        provider_id = identifier("launch:provider:" + provider)
        model_id = identifier("launch:model:" + model)
        profile_id = identifier("launch:profile:" + model)
        sql += insert("provider_catalog", {
            "id": provider_id, "code": provider, "status": "staged",
            "canonical_base_url": "https://generativelanguage.googleapis.com" if provider == "gemini" else "https://api.openai.com/v1",
        }, "code")
        sql += insert("ai_model_configurations", {
            "id": model_id, "provider_id": provider_id, "identifier": model,
            "revision": 1, "capabilities": '["embedding"]', "adapter": "dual_embedding",
            "adapter_version": "1", "status": "staged",
        }, "provider_id,identifier,revision")
        sql += insert("embedding_profiles", {
            "id": profile_id, "provider_id": provider_id, "model_id": model_id,
            "model_revision": 1, "dimension": dimension, "document_task_type": document_task,
            "query_task_type": query_task, "normalization": "l2", "generation": 1,
            "status": "staged",
        }, "provider_id,model_id,model_revision,generation")
        for scope in SCOPES:
            name = env.get(f"ASTRA_COLLECTION_{scope.upper()}_{provider.upper()}")
            if not name or not re.fullmatch(r"dev_temanbule_[a-z0-9_]+", name):
                raise SystemExit("Missing or non-development collection binding")
            collections.append((name, dimension))
            sql += insert("vector_collection_registry", {
                "id": identifier("launch:collection:" + name), "environment": "development",
                "scope": scope, "profile_id": profile_id, "physical_name": name,
                "similarity_metric": "cosine", "metadata_index_policy": '{"deny":["text"]}',
                "policy_version": "launch-20260928", "status": "staged",
            }, "environment,scope,profile_id")
    # Metadata existence verified against the account on 2026-09-28.
    # Keep staged until invocation/metering and deployed runtime binding pass.
    for model, capability in (
        ("gemini-3.8-flash", "llm"),
        ("gemini-3.5-transcribe", "stt"),
    ):
        sql += insert("ai_model_configurations", {
            "id": identifier("launch:model:" + model),
            "provider_id": identifier("launch:provider:gemini"),
            "identifier": model, "revision": 1,
            "capabilities": json.dumps([capability]),
            "adapter": "gemini_generate_content", "adapter_version": "1",
            "limits": json.dumps({
                "metadata_verified_on": "2026-09-28",
                "invocation_verified": False,
                "realtime_verified": False,
            }, sort_keys=True),
            "status": "staged",
        }, "provider_id,identifier,revision")
    for code in ("elean", "willy"):
        artifact = Path(f".blueprint/personas/{code}.v1.md")
        content_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        voice = env.get("TTS_VOICE_ID_" + code.upper())
        if not voice:
            raise SystemExit("Missing persona voice binding")
        # Resolve existing agent by natural key; preserve its historical identity.
        agent_id = identifier("launch:agent:" + code)
        version_id = identifier("launch:persona:" + code)
        voice_id = identifier("launch:voice:" + code)
        sql += f"INSERT INTO agents(id,code,display_name,status) VALUES ({literal(agent_id)},{literal(code)},{literal(code.title())},'active') ON CONFLICT(code) DO NOTHING;\n"
        sql += f"""
DO $$ DECLARE aid varchar(26); BEGIN
SELECT id INTO STRICT aid FROM agents WHERE code={literal(code)};
INSERT INTO voice_configuration_versions
(id,agent_id,revision,provider,model_identifier,voice_identifier,status)
VALUES ({literal(voice_id)},aid,20260928,'elevenlabs','eleven_v3',{literal(voice)},'published')
ON CONFLICT(agent_id,revision) DO NOTHING;
IF NOT EXISTS (SELECT 1 FROM voice_configuration_versions WHERE id={literal(voice_id)}
AND agent_id=aid AND model_identifier='eleven_v3' AND voice_identifier={literal(voice)})
THEN RAISE EXCEPTION 'Voice seed conflict'; END IF;
INSERT INTO agent_versions
(id,agent_id,revision,persona_artifact_ref,persona_artifact_hash,voice_binding_ref,status)
VALUES ({literal(version_id)},aid,20260928,{literal(str(artifact))},{literal(content_hash)},
{literal(voice_id)},'published') ON CONFLICT(agent_id,revision) DO NOTHING;
IF NOT EXISTS (SELECT 1 FROM agent_versions WHERE id={literal(version_id)} AND agent_id=aid
AND persona_artifact_hash={literal(content_hash)} AND voice_binding_ref={literal(voice_id)})
THEN RAISE EXCEPTION 'Persona seed conflict'; END IF;
UPDATE agents SET active_version_id={literal(version_id)} WHERE id=aid AND
(active_version_id IS NULL OR NOT EXISTS
 (SELECT 1 FROM agent_versions WHERE id=agents.active_version_id AND revision>20260928));
END $$;
"""
    rubric = json.loads(Path(".blueprint/toefl-practice.v1.json").read_text())
    sql += insert("toefl_test_versions", {
        "id": identifier("launch:toefl:writing:v1"),
        "code": "academic-writing-practice", "revision": 1,
        "rubric_version": rubric["rubric_version"],
        "definition": json.dumps(rubric, sort_keys=True),
        "publication_state": "draft",
    }, "code,revision")
    sql += "COMMIT;\n"
    print("Plan: 2 policies, 4 packages, 2 providers/models/profiles, 10 vector bindings.")
    if not args.apply:
        print("Dry run only. Use --apply to provision empty Astra collections and seed PostgreSQL.")
        return
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        url = f"{endpoint}/api/json/v1/{namespace}"

        def command(payload):
            response = client.post(url, headers={"Token": str(env["ASTRA_DB_APPLICATION_TOKEN"])}, json=payload)
            if response.status_code != 200:
                raise RuntimeError(f"Astra HTTP {response.status_code}")
            body = response.json()
            if body.get("errors"):
                raise RuntimeError("Astra command rejected; no SQL changes applied")
            return body

        body = command({"findCollections": {"options": {"explain": True}}})
        existing = {item["name"]: item for item in body["status"]["collections"]}
        # Validate every existing collection before any remote write.
        for name, dimension in collections:
            if name in existing:
                options = existing[name].get("options", {})
                vector = options.get("vector", {})
                if vector.get("dimension") != dimension or vector.get("metric") != "cosine":
                    raise RuntimeError("Astra vector configuration conflict: " + name)
                if options.get("indexing", {}) != {"deny": ["text"]}:
                    raise RuntimeError("Astra indexing configuration conflict: " + name)
        for name, dimension in collections:
            if name not in existing:
                command({"createCollection": {"name": name, "options": {
                    "vector": {"dimension": dimension, "metric": "cosine"},
                    "indexing": {"deny": ["text"]},
                }}})
                print("Created Astra collection:", name)
            else:
                print("Verified Astra collection:", name)
    result = subprocess.run(
        ["docker", "compose", "--env-file", ".env", "--env-file", ".env.deploy", "exec", "-T",
         "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U", str(env.get("POSTGRES_USER") or "temanbule"),
         "-d", str(env.get("POSTGRES_DB") or "temanbule")],
        input=sql, text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError("PostgreSQL seed rolled back; check schema/natural-key conflicts locally")
    print("PostgreSQL seed committed. Vector profiles remain staged until live embedding verification.")


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, RuntimeError) as exc:
        # Transport exceptions can contain credential-bearing URLs; print only type.
        print(str(exc) if isinstance(exc, RuntimeError) else "Vendor transport failed")
        raise SystemExit(1) from None
