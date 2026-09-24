#!/usr/bin/env python3
"""Generate RSA keypairs untuk development (AUTH_JWT dan execution token).

Usage: .venv/bin/python scripts/generate_keys.py [output_dir]
Default output: ./secrets/dev
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Pastikan src/ ada di path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from temanbule.platform.security import generate_rsa_keypair  # noqa: E402


def main() -> None:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "secrets/dev")
    base.mkdir(parents=True, exist_ok=True)
    pairs = {
        "auth_jwt": ("auth_jwt_private.pem", "auth_jwt_public.pem"),
        "execution_token": ("execution_token_private.pem", "execution_token_public.pem"),
    }
    for name, (priv, pub) in pairs.items():
        priv_path = base / priv
        pub_path = base / pub
        if priv_path.exists() or pub_path.exists():
            print(f"[skip] {name}: sudah ada di {base}")
            continue
        generate_rsa_keypair(priv_path, pub_path)
        os.chmod(priv_path, 0o600)
        os.chmod(pub_path, 0o644)
        print(f"[ok] {name}: {priv_path} / {pub_path}")


if __name__ == "__main__":
    main()
