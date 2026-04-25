"""
API key management for Hako Foundry.

Keys are stored as SHA-256 hashes in config/api_config.json.
The raw key is returned only once at creation time and never persisted.
"""

import hashlib
import json
import logging
import os
import secrets
import stat
import time
import uuid
from typing import Optional

API_CONFIG_FILE = "config/api_config.json"
logger = logging.getLogger("foundry_logger")


class ApiKeyManager:
    def __init__(self):
        self._ensure_config_dir()
        self._cache: Optional[dict] = None

    def _ensure_config_dir(self):
        """Ensure the config directory exists before any read/write."""
        config_dir = os.path.dirname(API_CONFIG_FILE)
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)

    def _load_config(self) -> dict:
        """Load key config from disk (cached after first read)."""
        if self._cache is not None:
            return self._cache
        try:
            if os.path.exists(API_CONFIG_FILE):
                with open(API_CONFIG_FILE, "r") as f:
                    self._cache = json.load(f)
                    return self._cache
        except Exception as e:
            logger.error(f"Error loading API key config: {e}")
        self._cache = {"keys": []}
        return self._cache

    def _save_config(self, config: dict):
        """Persist key config to disk with owner-only (600) permissions, and update cache."""
        try:
            with open(API_CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=4)
            try:
                os.chmod(API_CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)
            except OSError as e:
                logger.warning(f"Could not set permissions on API key config: {e}")
            self._cache = config
        except Exception as e:
            logger.error(f"Error saving API key config: {e}")

    @staticmethod
    def _hash(key: str) -> str:
        """Return the SHA-256 hex digest of a key."""
        return hashlib.sha256(key.encode()).hexdigest()

    def list_keys(self) -> list:
        """Return key metadata (id, name, prefix, created_at) — no secrets."""
        return [
            {
                "id": k["id"],
                "name": k["name"],
                "prefix": k["prefix"],
                "created_at": k["created_at"],
            }
            for k in self._load_config().get("keys", [])
        ]

    def create_key(self, name: str) -> str:
        """Generate, store, and return a new API key. Shown to the user exactly once."""
        raw = "hf-api_" + secrets.token_urlsafe(32)
        new_entry = {
            "id": str(uuid.uuid4()),
            "name": name,
            "prefix": raw[:12],  # "hf-api_XXXXX" — enough for identification
            "hash": self._hash(raw),
            "created_at": time.time(),
        }
        new_config = {"keys": self._load_config().get("keys", []) + [new_entry]}
        self._save_config(new_config)
        logger.info(f"API key created: '{name}'")
        return raw

    def delete_key(self, key_id: str) -> bool:
        """Remove a key by id. Returns True if a key was removed, False if not found."""
        existing = self._load_config().get("keys", [])
        new_keys = [k for k in existing if k["id"] != key_id]
        if len(new_keys) == len(existing):
            return False
        self._save_config({"keys": new_keys})
        logger.info(f"API key deleted: {key_id}")
        return True

    def validate_key(self, key: str) -> bool:
        """Return True if the key matches any stored key (timing-safe, no early exit)."""
        if not key:
            return False
        key_hash = self._hash(key)
        matched = False
        for k in self._load_config().get("keys", []):
            stored = k.get("hash", "")
            if stored and secrets.compare_digest(key_hash, stored):
                matched = True
        return matched


api_key_manager = ApiKeyManager()
