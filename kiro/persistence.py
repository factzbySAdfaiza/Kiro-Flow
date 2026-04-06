# -*- coding: utf-8 -*-

"""
Persistence layer for Kiro Gateway.
Handles saving and loading of Kiro credentials (tokens, profile ARN, etc.)
Supports both local file storage and Firebase Firestore.
"""

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional

from loguru import logger

try:
    from google.cloud import firestore
    FIRESTORE_AVAILABLE = True
except ImportError:
    FIRESTORE_AVAILABLE = False


class BasePersistence(ABC):
    """Base class for all persistence providers."""

    @abstractmethod
    async def get_user_keys(self, uid: str) -> list[Dict[str, Any]]:
        """Get all API keys for a specific user."""
        pass

    @abstractmethod
    async def add_user_key(self, uid: str, name: str, value: str, key_id: str) -> bool:
        """Add a new API key for a user and update global lookup."""
        pass

    @abstractmethod
    async def delete_user_key(self, uid: str, key_id: str) -> bool:
        """Delete an API key for a user."""
        pass

    @abstractmethod
    async def validate_api_key(self, value: str) -> Optional[Dict[str, Any]]:
        """Validate an API key and return owner info if valid."""
        pass


class FilePersistence(BasePersistence):
    """Local file-based persistence provider."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path).expanduser()
        logger.info(f"Using FilePersistence: {self.file_path}")

    async def load(self) -> Dict[str, Any]:
        if not self.file_path.exists():
            return {}
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading credentials from file: {e}")
            return {}

    async def save(self, data: Dict[str, Any]) -> bool:
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            existing = await self.load()
            existing.update(data)
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Error saving to file: {e}")
            return False

    async def get_user_keys(self, uid: str) -> list[Dict[str, Any]]:
        data = await self.load()
        users = data.get("users", {})
        user_data = users.get(uid, {})
        return user_data.get("keys", [])

    async def add_user_key(self, uid: str, name: str, value: str, key_id: str) -> bool:
        data = await self.load()
        users = data.setdefault("users", {})
        user_data = users.setdefault(uid, {"keys": []})
        
        from datetime import datetime
        new_key = {
            "id": key_id,
            "name": name,
            "value": value, # In multi-user mode, we store full keys for simplicity in local file
            "created_at": datetime.now().isoformat()
        }
        user_data["keys"].append(new_key)
        
        # Global lookup for local file is just iterating, but we'll store a separate index for speed
        lookup = data.setdefault("api_keys_lookup", {})
        lookup[value] = {"uid": uid, "key_id": key_id, "name": name}
        
        return await self.save(data)

    async def delete_user_key(self, uid: str, key_id: str) -> bool:
        data = await self.load()
        users = data.get("users", {})
        user_data = users.get(uid)
        if not user_data: return False
        
        keys = user_data.get("keys", [])
        key_to_delete = next((k for k in keys if k["id"] == key_id), None)
        if not key_to_delete: return False
        
        user_data["keys"] = [k for k in keys if k["id"] != key_id]
        
        lookup = data.get("api_keys_lookup", {})
        if key_to_delete["value"] in lookup:
            del lookup[key_to_delete["value"]]
            
        return await self.save(data)

    async def validate_api_key(self, value: str) -> Optional[Dict[str, Any]]:
        data = await self.load()
        lookup = data.get("api_keys_lookup", {})
        return lookup.get(value)


def _parse_service_account(data: str) -> Optional[Dict[str, Any]]:
    """Robustly parse service account JSON string, handling escaping."""
    if not data: return None
    try:
        # 1. Direct parse
        return json.loads(data)
    except json.JSONDecodeError:
        try:
            # 2. Handle escaped newlines (\n) often found in Vercel/Docker env vars
            cleaned = data.replace('\\n', '\n')
            return json.loads(cleaned)
        except Exception as e:
            logger.error(f"Failed to parse FIREBASE_SERVICE_ACCOUNT JSON: {e}")
            return None

class FirebasePersistence(BasePersistence):
    """Firebase Firestore-based persistence provider."""

    def __init__(self, project_id: str, collection: str, document_id: str):
        if not FIRESTORE_AVAILABLE:
            raise ImportError("google-cloud-firestore not installed.")
        
        from kiro.config import FIREBASE_SERVICE_ACCOUNT, FIREBASE_SERVICE_ACCOUNT_FILE
        
        # 1. Try explicit JSON string
        info = _parse_service_account(FIREBASE_SERVICE_ACCOUNT)
        if info:
            try:
                self.db = firestore.AsyncClient.from_service_account_info(info)
                logger.info("Firebase Firestore initialized from environment JSON string")
            except Exception as e:
                logger.error(f"Failed to init Firestore from JSON info: {e}")
                self.db = None
        # 2. Try explicit JSON file
        elif FIREBASE_SERVICE_ACCOUNT_FILE and os.path.exists(FIREBASE_SERVICE_ACCOUNT_FILE):
             try:
                self.db = firestore.AsyncClient.from_service_account_json(FIREBASE_SERVICE_ACCOUNT_FILE)
                logger.info(f"Firebase Firestore initialized from file: {FIREBASE_SERVICE_ACCOUNT_FILE}")
             except Exception as e:
                logger.error(f"Failed to init Firestore from file: {e}")
                self.db = None
        # 3. Try pulling from initialized firebase_admin
        else:
            try:
                import firebase_admin
                app = firebase_admin.get_app()
                # If initialized with certificate, we can try to reuse it
                if hasattr(app.credential, 'service_account_info'):
                    self.db = firestore.AsyncClient.from_service_account_info(app.credential.service_account_info)
                    logger.info("Firebase Firestore initialized using firebase_admin credentials")
                else:
                    # Fallback to default, but handle crash
                    if os.getenv("VERCEL") == "1":
                        logger.error("Vercel Detected: Cannot initialize Firestore without explicit credentials.")
                    try:
                        self.db = firestore.AsyncClient(project=project_id)
                    except Exception as e:
                        logger.error(f"Firestore Default Init Failed (expected on Vercel Hobby): {e}")
                        self.db = None
            except Exception as e:
                logger.warning(f"Could not bridge credentials from firebase_admin: {e}")
                try:
                    self.db = firestore.AsyncClient(project=project_id)
                except Exception:
                    self.db = None

        self.collection = collection
        self.document_id = document_id

    async def load(self) -> Dict[str, Any]:
        if not self.db: return {}
        try:
            doc_ref = self.db.collection(self.collection).document(self.document_id)
            doc = await doc_ref.get()
            return doc.to_dict() if doc.exists else {}
        except Exception as e:
            logger.error(f"Error loading from Firestore: {e}")
            return {}

    async def save(self, data: Dict[str, Any]) -> bool:
        if not self.db: return False
        try:
            doc_ref = self.db.collection(self.collection).document(self.document_id)
            await doc_ref.set(data, merge=True)
            return True
        except Exception as e:
            logger.error(f"Error saving to Firestore: {e}")
            return False

    async def get_user_keys(self, uid: str) -> list[Dict[str, Any]]:
        if not self.db: return []
        try:
            keys_ref = self.db.collection("users").document(uid).collection("keys")
            docs = await keys_ref.get()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            logger.error(f"Error getting user keys: {e}")
            return []

    async def add_user_key(self, uid: str, name: str, value: str, key_id: str) -> bool:
        if not self.db: return False
        try:
            from datetime import datetime
            key_data = {
                "id": key_id,
                "name": name,
                "value": value,
                "created_at": datetime.utcnow().isoformat()
            }
            
            # 1. Add to user's keys sub-collection
            user_key_ref = self.db.collection("users").document(uid).collection("keys").document(key_id)
            await user_key_ref.set(key_data)
            
            # 2. Add to global lookup table for fast validation
            lookup_ref = self.db.collection("api_keys_lookup").document(value)
            await lookup_ref.set({
                "uid": uid,
                "key_id": key_id,
                "name": name
            })
            return True
        except Exception as e:
            logger.error(f"Error adding user key: {e}")
            return False

    async def delete_user_key(self, uid: str, key_id: str) -> bool:
        if not self.db: return False
        try:
            # Get the key first to find its value for lookup deletion
            user_key_ref = self.db.collection("users").document(uid).collection("keys").document(key_id)
            doc = await user_key_ref.get()
            if not doc.exists: return False
            
            value = doc.to_dict().get("value")
            
            # 1. Delete from user
            await user_key_ref.delete()
            
            # 2. Delete from global lookup
            if value:
                await self.db.collection("api_keys_lookup").document(value).delete()
            return True
        except Exception as e:
            logger.error(f"Error deleting user key: {e}")
            return False

    async def validate_api_key(self, value: str) -> Optional[Dict[str, Any]]:
        if not self.db: return None
        try:
            lookup_ref = self.db.collection("api_keys_lookup").document(value)
            doc = await lookup_ref.get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            logger.error(f"Error validating API key in Firestore: {e}")
            return None


def get_persistence_provider() -> BasePersistence:
    from kiro.config import FIREBASE_PROJECT_ID, FIREBASE_COLLECTION, FIREBASE_DOCUMENT_ID, KIRO_CREDS_FILE
    
    if FIREBASE_PROJECT_ID:
        return FirebasePersistence(FIREBASE_PROJECT_ID, FIREBASE_COLLECTION, FIREBASE_DOCUMENT_ID)
    
    if KIRO_CREDS_FILE:
        return FilePersistence(KIRO_CREDS_FILE)
    
    class DummyPersistence(BasePersistence):
        async def load(self) -> Dict[str, Any]: return {}
        async def save(self, data: Dict[str, Any]) -> bool: return True
        async def get_user_keys(self, uid: str) -> list[Dict[str, Any]]: return []
        async def add_user_key(self, uid, name, val, kid) -> bool: return True
        async def delete_user_key(self, uid, kid) -> bool: return True
        async def validate_api_key(self, val) -> Optional[Dict[str, Any]]: return None
    
    return DummyPersistence()
