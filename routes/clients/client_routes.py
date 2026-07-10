import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, Query, status, UploadFile, File, HTTPException
from typing import List, Optional, Dict
import uuid
from functions.schema_model import ClientCreate, ClientUpdate, ClientResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user, get_client_user
from functions.access_control import assert_client_owns, get_client_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.clients.client_functions import ClientFunctions
from routes.contracts.contract_functions import ContractFunctions
from functions.minio_client import upload_client_profile_picture, delete_file, BUCKET_USER_ASSETS, validate_file_size
from mimetypes import guess_type as guess_mime
from routes.admin.admin_moderation import scan_harmful_text, scan_harmful_text_with_ml_fallback, ML_SCAN_TIMEOUT_BLOCKING_SECONDS

_IDENTITY_FIELD_LABEL_NAMES = {
    "toxic": "toxicity",
    "toxicity": "toxicity",
    "obscene": "obscenity",
    "threat": "threats",
    "insult": "insults",
    "identity_hate": "identity-based hate speech",
}


def _reject_for(harm_result: Dict, field_label: str) -> Optional[Dict]:
    if not harm_result["is_flagged"]:
        return None
    labels = [_IDENTITY_FIELD_LABEL_NAMES.get(l, l) for l in harm_result.get("detected_labels", [])]
    logger("CLIENT", f"Blocked {field_label} save, labels={harm_result.get('detected_labels')}", level="WARNING")
    return {
        "message": f"Your {field_label} couldn't be saved. It was flagged for {', '.join(labels) or 'a policy violation'}.",
        "status": 400,
    }


async def _scan_identity_fields_or_reject(short_text: str, long_text: str, field_label: str = "profile") -> Optional[Dict]:
    """Scan-then-reject for name/bio, mirroring the DM message flow
    (routes/dm/dm_routes.py send_message).

    full_name carries no context - a 1-4 word field gives the ML model nothing to
    condition on, so it matches vocabulary rather than meaning. It goes through the
    deterministic keyword list only. bio has real sentence context, so it still goes
    through the ML model, and fails OPEN if that scan errors - blast radius is one
    profile, so blocking a legitimate save because the model crashed is worse than
    letting it through for a later re-scan pass."""
    if short_text and short_text.strip():
        rejection = _reject_for(scan_harmful_text(short_text), field_label)
        if rejection:
            return rejection

    if not long_text or not long_text.strip():
        return None
    try:
        harm_result = await scan_harmful_text_with_ml_fallback(long_text, timeout=ML_SCAN_TIMEOUT_BLOCKING_SECONDS)
    except Exception as e:
        logger("CLIENT", f"Identity-field scan errored, failing open (allowing save): {e}", level="WARNING")
        return None
    return _reject_for(harm_result, field_label)

client_router = APIRouter(prefix="/clients", tags=["Clients"])

_VALID_CLIENT_ORDER_BY = {"created_at", "updated_at", "full_name", "total_jobs_posted", "total_jobs_completed"}


@client_router.get("/browse/all", response_model=List[ClientResponse])
async def browse_all_clients(
    order_by: str = Query(
        default="created_at",
        description="Sort field. One of: created_at (default), updated_at, full_name, total_jobs_posted, total_jobs_completed",
    ),
    order_dir: str = Query(default="desc", description="asc or desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_current_user),
):
    """Browse all clients with pagination and sorting - Authenticated users only."""
    try:
        if order_by not in _VALID_CLIENT_ORDER_BY:
            return ResponseSchema.error(
                f"Invalid order_by '{order_by}'. Valid values: {', '.join(sorted(_VALID_CLIENT_ORDER_BY))}", 400
            )
        result = ClientFunctions.browse_clients(
            order_by=order_by,
            order_dir=order_dir,
            page=page,
            page_size=page_size,
        )
        logger("CLIENT", f"Browsed clients: page={page}", "GET /clients/browse/all", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        error_msg = f"Failed to fetch clients for browse: {str(e)}"
        logger("CLIENT", error_msg, "GET /clients/browse/all", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.get("", response_model=List[ClientResponse])
async def get_all_clients(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch current client profile - Authenticated users only - JSON response."""
    try:
        client = get_client_profile_for_user(current_user)
        success_msg = f"Retrieved client profile for user {current_user.user_id}"
        logger("CLIENT", success_msg, "GET /clients", "INFO")
        return ResponseSchema.success([client], 200)
    except HTTPException as e:
        logger("CLIENT", f"HTTP {e.status_code}: {e.detail}", "GET /clients", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Failed to fetch clients: {str(e)}"
        logger("CLIENT", error_msg, "GET /clients", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.get("/search", response_model=Dict)
async def search_clients(
    name: str = Query(..., description="Client name to search for"),
    current_user: UserInDB = Depends(get_current_user),
):
    """Search clients by full name - Authenticated users only - JSON response."""
    try:
        results = ClientFunctions.search_clients_by_full_name(name)
        logger("CLIENT", f"Searched clients for '{name}', found {len(results)} results", "GET /clients/search", "INFO")
        return ResponseSchema.success({"results": results, "count": len(results)}, 200)
    except Exception as e:
        error_msg = f"Failed to search clients with term '{name}': {str(e)}"
        logger("CLIENT", error_msg, "GET /clients/search", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.get("/{client_id}/reliability")
async def get_client_reliability(client_id: str, current_user: UserInDB = Depends(get_current_user)):
    """
    Qualitative signal for freelancers deciding whether to work with a client -
    'Kurang Responsif' once a client has let 2+ contracts auto-approve from
    inactivity (one strike away from the account being closed automatically).
    """
    try:
        client = ClientFunctions.get_client_by_id_or_user_id(client_id)
        if not client:
            return ResponseSchema.error(f"Client {client_id} not found", 404)

        label = ContractFunctions.get_client_reliability_label(str(client["user_id"]))
        logger("CLIENT", f"Retrieved reliability label for client {client_id}", "GET /clients/{client_id}/reliability", "INFO")
        return ResponseSchema.success({"label": label}, 200)
    except Exception as e:
        error_msg = f"Failed to fetch reliability for client {client_id}: {str(e)}"
        logger("CLIENT", error_msg, "GET /clients/{client_id}/reliability", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.get("/{identifier}", response_model=ClientResponse)
async def get_client(identifier: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single client by ID (supports both client_id and user_id) - Authenticated users only - JSON response."""
    try:
        client = ClientFunctions.get_client_by_id_or_user_id(identifier)
        if not client:
            error_msg = f"Client {identifier} not found"
            logger("CLIENT", error_msg, "GET /clients/{identifier}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved client {identifier}"
        logger("CLIENT", success_msg, "GET /clients/{identifier}", "INFO")
        return ResponseSchema.success(client, 200)
    except Exception as e:
        error_msg = f"Failed to fetch client {identifier}: {str(e)}"
        logger("CLIENT", error_msg, "GET /clients/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)

@client_router.post("", response_model=ClientResponse, status_code=201)
async def create_client(
    client: ClientCreate = Depends(),
    current_user: UserInDB = Depends(get_client_user),
):
    """Create a new client profile - Clients only - JSON body accepted."""
    try:
        # The client profile must be created for the authenticated user only
        client_id = client.client_id or str(uuid.uuid4())
        current_client = get_client_profile_for_user(current_user)
        if client.user_id and str(client.user_id) != str(current_user.user_id):
            return ResponseSchema.error("Cannot create a client profile for another user", 403)
        if current_client:
            error_msg = f"Client profile already exists for user {current_user.user_id}"
            logger("CLIENT", error_msg, "POST /clients", "WARNING")
            return ResponseSchema.error(error_msg, 400)

        profile_picture_url = None
        if client.profile_picture is not None:
            contents = await client.profile_picture.read()
            if not contents:
                return ResponseSchema.error("Profile picture file must not be empty", 400)
            validate_file_size(contents, client.profile_picture.filename or "profile picture")
            mime_type = client.profile_picture.content_type or guess_mime(client.profile_picture.filename or "avatar.jpg")[0]
            if not mime_type.startswith("image/"):
                return ResponseSchema.error("Only image files are allowed for profile pictures", 400)
            logger("CLIENT", f"Uploading client avatar for user {current_user.user_id}: filename={client.profile_picture.filename}, size={len(contents)} bytes, mime={mime_type}", level="DEBUG")
            profile_picture_url = upload_client_profile_picture(
                client_id=current_user.user_id,
                file_name=client.profile_picture.filename or "avatar.jpg",
                file_bytes=contents,
                content_type=mime_type,
            )
            logger("CLIENT", f"Client avatar uploaded: {profile_picture_url}", level="DEBUG")

        rejection = await _scan_identity_fields_or_reject(client.full_name, client.bio, "profile")
        if rejection:
            return ResponseSchema.error(rejection["message"], rejection["status"])

        new_client = ClientFunctions.create_client(
            client_id=client_id,
            user_id=current_user.user_id,
            full_name=client.full_name,
            bio=client.bio,
            website_url=client.website_url,
            profile_picture_url=profile_picture_url
        )

        success_msg = f"Created client {client_id} for user {client.user_id} with full name '{client.full_name}'"
        logger("CLIENT", success_msg, "POST /clients", "INFO")
        return ResponseSchema.success(new_client, 201)
    except HTTPException as e:
        logger("CLIENT", f"HTTP {e.status_code}: {e.detail}", "POST /clients", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Failed to create client: {str(e)}"
        logger("CLIENT", error_msg, "POST /clients", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.put("/{identifier}", response_model=ClientResponse)
async def update_client(
    identifier: str,
    client_update: ClientUpdate = Depends(ClientUpdate.as_form),
    current_user: UserInDB = Depends(get_client_user),
):
    """Update client information (supports both client_id and user_id) - Clients only."""
    try:
        # Check if client exists and get actual client_id if user_id was provided
        existing = ClientFunctions.get_client_by_id_or_user_id(identifier)
        if not existing:
            error_msg = f"Client {identifier} not found for update"
            logger("CLIENT", error_msg, "PUT /clients/{identifier}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        client_id = existing["client_id"]
        assert_client_owns(current_user, client_id)
        update_data = client_update.model_dump(
            exclude={"profile_picture"},
            exclude_unset=True,
        )

        if client_update.profile_picture is not None:
            contents = await client_update.profile_picture.read()
            if not contents:
                return ResponseSchema.error("Profile picture file must not be empty", 400)
            validate_file_size(contents, client_update.profile_picture.filename or "profile picture")
            mime_type = client_update.profile_picture.content_type or guess_mime(client_update.profile_picture.filename or "avatar.jpg")[0]
            if not mime_type.startswith("image/"):
                return ResponseSchema.error("Only image files are allowed for profile pictures", 400)
            logger("CLIENT", f"Uploading client avatar for user {current_user.user_id}: filename={client_update.profile_picture.filename}, size={len(contents)} bytes, mime={mime_type}", level="DEBUG")
            update_data["profile_picture_url"] = upload_client_profile_picture(
                client_id=current_user.user_id,
                file_name=client_update.profile_picture.filename or "avatar.jpg",
                file_bytes=contents,
                content_type=mime_type,
            )
            logger("CLIENT", f"Client avatar uploaded: {update_data['profile_picture_url']}", level="DEBUG")

        _short_text = update_data.get("full_name") if "full_name" in update_data else existing.get("full_name", "")
        _long_text = update_data.get("bio") if "bio" in update_data else existing.get("bio", "")
        rejection = await _scan_identity_fields_or_reject(_short_text, _long_text, "profile")
        if rejection:
            return ResponseSchema.error(rejection["message"], rejection["status"])

        updated_client = ClientFunctions.update_client(client_id, update_data)

        success_msg = f"Updated client {client_id} with fields: {', '.join(update_data.keys())}"
        logger("CLIENT", success_msg, "PUT /clients/{identifier}", "INFO")
        return ResponseSchema.success(updated_client, 200)
    except HTTPException as e:
        logger("CLIENT", f"HTTP {e.status_code}: {e.detail}", "PUT /clients/{identifier}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Failed to update client {identifier}: {str(e)}"
        logger("CLIENT", error_msg, "PUT /clients/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.delete("/{identifier}", status_code=200)
async def delete_client(identifier: str, current_user: UserInDB = Depends(get_client_user)):
    """Delete a client profile (supports both client_id and user_id) - Clients only."""
    try:
        # Check if client exists and get actual client_id if user_id was provided
        existing = ClientFunctions.get_client_by_id_or_user_id(identifier)
        if not existing:
            error_msg = f"Client {identifier} not found for deletion"
            logger("CLIENT", error_msg, "DELETE /clients/{identifier}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        client_id = existing["client_id"]
        assert_client_owns(current_user, client_id)
        ClientFunctions.delete_client(client_id)
        success_msg = f"Client {client_id} deleted successfully"
        logger("CLIENT", success_msg, "DELETE /clients/{identifier}", "INFO")
        return ResponseSchema.success(success_msg, 200)
    except HTTPException as e:
        logger("CLIENT", f"HTTP {e.status_code}: {e.detail}", "DELETE /clients/{identifier}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Failed to delete client {identifier}: {str(e)}"
        logger("CLIENT", error_msg, "DELETE /clients/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.post("/{client_id}/profile-picture", response_model=ClientResponse)
async def upload_client_profile_picture_endpoint(
    client_id: str,
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_client_user),
):
    try:
        existing = ClientFunctions.get_client_by_id_or_user_id(client_id)
        if not existing:
            error_msg = f"Client {client_id} not found"
            logger("CLIENT", error_msg, f"POST /clients/{client_id}/profile-picture", "WARNING")
            return ResponseSchema.error(error_msg, 404)

        assert_client_owns(current_user, existing["client_id"])

        contents = await file.read()
        if not contents:
            return ResponseSchema.error("Profile picture file must not be empty", 400)
        validate_file_size(contents, file.filename or "profile picture")

        mime_type = file.content_type or guess_mime(file.filename or "avatar.jpg")[0]
        if not mime_type or not mime_type.startswith("image/"):
            return ResponseSchema.error("Only image files are allowed for profile pictures", 400)

        profile_picture_url = upload_client_profile_picture(
            client_id=current_user.user_id,
            file_name=file.filename or "avatar.jpg",
            file_bytes=contents,
            content_type=mime_type,
        )
        updated_client = ClientFunctions.update_client(
            existing["client_id"],
            {"profile_picture_url": profile_picture_url},
        )
        logger("CLIENT", f"Profile picture updated for client {client_id}", f"POST /clients/{client_id}/profile-picture", "INFO")
        return ResponseSchema.success(updated_client, 200)
    except HTTPException as e:
        logger("CLIENT", f"HTTP {e.status_code}: {e.detail}", f"POST /clients/{client_id}/profile-picture", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Failed to upload profile picture for client {client_id}: {str(e)}"
        logger("CLIENT", error_msg, f"POST /clients/{client_id}/profile-picture", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.delete("/{client_id}/profile-picture", status_code=200)
async def delete_client_profile_picture(
    client_id: str,
    current_user: UserInDB = Depends(get_client_user),
):
    try:
        existing = ClientFunctions.get_client_by_id_or_user_id(client_id)
        if not existing:
            return ResponseSchema.error(f"Client {client_id} not found", 404)
        assert_client_owns(current_user, existing["client_id"])

        profile_picture_url = existing.get("profile_picture_url")
        if not profile_picture_url:
            return ResponseSchema.error("No profile picture to delete", 400)

        # Extract path from URL
        if "user-assets/" in profile_picture_url:
            path = profile_picture_url.split("user-assets/")[-1]
        else:
            path = f"avatars/{current_user.user_id}.jpg"

        try:
            delete_file(BUCKET_USER_ASSETS, path)
        except Exception as e:
            logger("CLIENT", f"Failed to delete file from storage: {str(e)}", level="WARNING")

        updated = ClientFunctions.update_client(
            existing["client_id"],
            {"profile_picture_url": None},
        )
        logger("CLIENT", f"Profile picture deleted for client {client_id}", f"DELETE /clients/{client_id}/profile-picture", "INFO")
        return ResponseSchema.success({"message": "Profile picture deleted successfully", "client": updated}, 200)
    except HTTPException as e:
        logger("CLIENT", f"HTTP {e.status_code}: {e.detail}", f"DELETE /clients/{client_id}/profile-picture", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Failed to delete profile picture for client {client_id}: {str(e)}"
        logger("CLIENT", error_msg, f"DELETE /clients/{client_id}/profile-picture", "ERROR")
        return ResponseSchema.error(error_msg, 500)
