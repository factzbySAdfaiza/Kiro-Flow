from fastapi import APIRouter, HTTPException, Request, Depends, Form, Header
from fastapi.responses import JSONResponse, HTMLResponse
from typing import Optional, Dict, Any
from loguru import logger
import os
import firebase_admin
from firebase_admin import auth, credentials

from kiro.config import (
    FIREBASE_ADMIN_UID, 
    FIREBASE_PROJECT_ID, 
    FIREBASE_CLIENT_CONFIG, 
    APP_VERSION,
    FIREBASE_SERVICE_ACCOUNT,
    FIREBASE_SERVICE_ACCOUNT_FILE
)
from kiro.auth import KiroAuthManager
import json

router = APIRouter(prefix="/admin", tags=["admin"])

# Flag to track if Firebase Auth is fully functional with credentials
IS_FIREBASE_AUTH_READY = False

def _clean_json_str(data: str) -> Optional[str]:
    """Handles escaped newlines in env vars."""
    if not data: return None
    try:
        json.loads(data)
        return data
    except json.JSONDecodeError:
        try:
            cleaned = data.replace('\\n', '\n')
            json.loads(cleaned)
            return cleaned
        except Exception:
            return None

# Initialize firebase-admin only if not already initialized
try:
    firebase_admin.get_app()
    IS_FIREBASE_AUTH_READY = True
except ValueError:
    # 1. Try FIREBASE_SERVICE_ACCOUNT (JSON string)
    cleaned_json = _clean_json_str(FIREBASE_SERVICE_ACCOUNT)
    if cleaned_json:
        try:
            creds_dict = json.loads(cleaned_json)
            cred = credentials.Certificate(creds_dict)
            firebase_admin.initialize_app(cred)
            IS_FIREBASE_AUTH_READY = True
            logger.info("Firebase Admin initialized from environment JSON string")
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin from JSON: {e}")
    
    # 2. Try FIREBASE_SERVICE_ACCOUNT_FILE (Local path)
    if not IS_FIREBASE_AUTH_READY:
        # Check explicit path from env, or default 'firebase-key.json' in root
        local_key_file = FIREBASE_SERVICE_ACCOUNT_FILE or "firebase-key.json"
        if os.path.exists(local_key_file):
            try:
                cred = credentials.Certificate(local_key_file)
                firebase_admin.initialize_app(cred)
                IS_FIREBASE_AUTH_READY = True
                logger.info(f"Firebase Admin initialized from local file: {local_key_file}")
            except Exception as e:
                logger.error(f"Failed to initialize Firebase Admin from file {local_key_file}: {e}")
            
    # 3. Fallback to Project ID (Safe Limbo Mode)
    if not IS_FIREBASE_AUTH_READY:
        if FIREBASE_PROJECT_ID:
            try:
                # This allows the app to boot without auth features
                firebase_admin.initialize_app(options={'projectId': FIREBASE_PROJECT_ID})
                logger.warning(f"Firebase Admin in 'Limbo Mode' (Project ID: {FIREBASE_PROJECT_ID}). Admin features will be disabled.")
            except Exception as e:
                logger.error(f"Firebase Admin project ID fallback failed: {e}")
        else:
            # Last resort: try default initialization, but don't mark as READY unless successful
            try:
                firebase_admin.initialize_app()
                # Check if it actually has credentials by attempting to get some dummy property
                # but for simplicity, we just won't mark it as ready if we reach here without explicit credentials.
                logger.warning("Firebase Admin initialized with default credentials (ADC). Safety check may fail later.")
                # We'll set it to READY only if the user explicitly set FIREBASE_PROJECT_ID 
                # or we are in a cloud environment that supports ADC.
                if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                    IS_FIREBASE_AUTH_READY = True
                    logger.info("Firebase Admin is READY via Application Default Credentials (ADC)")
            except Exception as e:
                logger.warning(f"Firebase Admin default init failed: {e}")

async def get_current_user(authorization: Optional[str] = Header(None)):
    """
    Verifies the Firebase ID token for any user.
    """
    if not IS_FIREBASE_AUTH_READY:
        logger.error("Auth attempted but Firebase credentials are missing in environment.")
        raise HTTPException(
            status_code=401, 
            detail="Kiro-Flow: Database credentials not found. Please set FIREBASE_SERVICE_ACCOUNT in your Vercel Dashboard to enable this feature."
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    
    id_token = authorization.split("Bearer ")[1]
    
    import asyncio
    max_retries = 2
    delay = 1 # seconds
    
    for attempt in range(max_retries + 1):
        try:
            decoded_token = auth.verify_id_token(id_token)
            return decoded_token
        except Exception as e:
            if "Token used too early" in str(e) and attempt < max_retries:
                await asyncio.sleep(delay)
                continue
            logger.error(f"User token verification failed: {e}")
            raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

async def get_current_admin(user: Dict[str, Any] = Depends(get_current_user)):
    """
    Restrictions: Only allows the configured Admin UID.
    """
    uid = user.get("uid")
    if not FIREBASE_ADMIN_UID or uid != FIREBASE_ADMIN_UID:
        logger.warning(f"FORBIDDEN: UID {uid} tried to access admin-only route")
        raise HTTPException(status_code=403, detail="Forbidden: Admin access required")
    return user

def get_auth_manager():
    if auth_manager is None:
        raise HTTPException(status_code=500, detail="Auth manager not initialized")
    return auth_manager

# --- User Endpoints ---

@router.get("/user/info")
async def get_user_info(user: Dict[str, Any] = Depends(get_current_user)):
    """Returns basic user info and admin status."""
    return {
        "uid": user.get("uid"),
        "email": user.get("email"),
        "is_admin": user.get("uid") == FIREBASE_ADMIN_UID,
        "app_version": APP_VERSION
    }

@router.get("/user/keys")
async def list_user_keys(
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """List all API keys for the current user."""
    persistence = getattr(request.app.state, 'persistence', None)
    if not persistence:
        raise HTTPException(status_code=500, detail="Persistence not configured")
    
    keys = await persistence.get_user_keys(user["uid"])
    return {"keys": keys}

@router.post("/user/keys")
async def generate_user_key(
    request: Request,
    name: str = Form(...),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Generate a new unique API key for the user."""
    import secrets
    import uuid
    
    persistence = getattr(request.app.state, 'persistence', None)
    if not persistence:
        raise HTTPException(status_code=500, detail="Persistence not configured")
    
    # Generate a secure key in format: kiro_xxxx...
    new_key_value = f"kiro_{secrets.token_urlsafe(32)}"
    key_id = str(uuid.uuid4())
    
    success = await persistence.add_user_key(user["uid"], name, new_key_value, key_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save API key")
        
    return {
        "status": "success",
        "key": {
            "id": key_id,
            "name": name,
            "value": new_key_value  # Return full value ONLY ONCE during creation
        }
    }

@router.delete("/user/keys/{key_id}")
async def revoke_user_key(
    key_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Deletes/Revokes an API key."""
    persistence = getattr(request.app.state, 'persistence', None)
    if not persistence:
        raise HTTPException(status_code=500, detail="Persistence not configured")
    
    success = await persistence.delete_user_key(user["uid"], key_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found or already deleted")
        
    return {"status": "success", "message": "Key revoked"}

# --- Admin-Only Endpoints ---

@router.get("/config")
async def get_firebase_config():
    """Provides Firebase client configuration to the frontend (Public)."""
    config = FIREBASE_CLIENT_CONFIG.copy()
    config["firebase_ready"] = IS_FIREBASE_AUTH_READY
    return config

@router.get("/status")
async def get_status(
    admin: Dict[str, Any] = Depends(get_current_admin), 
    manager: KiroAuthManager = Depends(get_auth_manager)
):
    # Only Admin can see system-wide status
    return {
        "app_version": APP_VERSION,
        "admin_email": admin.get("email"),
        "auth_status": {
            "is_authenticated": manager._access_token is not None,
            "auth_type": manager._auth_type.value if manager._auth_type else "None",
            "region": manager._region,
            "expires_at": manager._expires_at.isoformat() if manager._expires_at else None,
            "has_refresh_token": manager._refresh_token is not None,
            "has_client_id": manager._client_id is not None
        }
    }

@router.post("/update-credentials")
async def update_credentials(
    request: Request,
    refresh_token: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None),
    region: Optional[str] = Form(None),
    admin: Dict[str, Any] = Depends(get_current_admin),
    manager: KiroAuthManager = Depends(get_auth_manager)
):
    updated = False
    if refresh_token:
        manager._refresh_token = refresh_token
        updated = True
    if client_id:
        manager._client_id = client_id
        updated = True
    if client_secret:
        manager._client_secret = client_secret
        updated = True
    if region:
        manager._region = region
        updated = True
    
    if updated:
        manager._detect_auth_type()
        await manager.save_to_persistence()
        try:
            manager._access_token = None
            manager._expires_at = None
            token = await manager.get_access_token()
            
            try:
                from kiro.utils import get_kiro_headers
                from kiro.auth import AuthType
                import httpx
                
                headers = get_kiro_headers(manager, token)
                params = {"origin": "AI_EDITOR"}
                if manager.auth_type == AuthType.KIRO_DESKTOP and manager.profile_arn:
                    params["profileArn"] = manager.profile_arn
                
                list_models_url = f"{manager.q_host}/ListAvailableModels"
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(list_models_url, headers=headers, params=params)
                    if response.status_code == 200:
                        models_list = response.json().get("models", [])
                        cache = getattr(request.app.state, 'model_cache', None)
                        if cache:
                            await cache.update(models_list)
            except Exception as e:
                logger.warning(f"Background model refresh failed: {e}")
            
            return {"status": "success", "message": "Credentials updated and token refreshed"}
        except Exception as e:
            return {"status": "warning", "message": f"Credentials updated but refresh failed: {str(e)}"}
    
    return {"status": "no_change", "message": "No credentials provided to update"}

@router.post("/refresh-token")
async def trigger_refresh(
    admin: Dict[str, Any] = Depends(get_current_admin), 
    manager: KiroAuthManager = Depends(get_auth_manager)
):
    try:
        manager._access_token = None
        manager._expires_at = None
        await manager.get_access_token()
        return {"status": "success", "message": "Token refreshed successfully"}
    except Exception as e:
        logger.error(f"Manual refresh failed: {e}")
        raise HTTPException(status_code=500, detail=f"Refresh failed: {str(e)}")
