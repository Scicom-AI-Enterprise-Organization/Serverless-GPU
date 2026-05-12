"""Fernet encrypt/decrypt for at-rest secrets (SSH private keys, API tokens).

Reads the symmetric key from `PROVIDER_SECRET_KEY` env. Generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = (os.environ.get("PROVIDER_SECRET_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "PROVIDER_SECRET_KEY not set — generate one with "
            "`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`"
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("could not decrypt — PROVIDER_SECRET_KEY changed?") from e
