import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import ClientCreate, ClientUpdate, ClientResponse
from functions.schema_model import UserInDB
from functions.authentication import get_client_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.clients.client_functions import ClientFunctions

client_router = APIRouter(prefix="/clients", tags=["Clients"])


@client_router.get("", response_model=List[ClientResponse])
async def get_all_clients(limit: Optional[int] = None, current_user: UserInDB = Depends(get_client_user)):
    """Fetch all clients - Clients only - JSON response"""
    try:
        clients = ClientFunctions.get_all_clients(limit=limit)
        success_msg = f"Retrieved {len(clients)} clients" + (f" (limit: {limit})" if limit else "")
        logger("CLIENT", success_msg, "GET /clients", "INFO")
        return ResponseSchema.success(clients, 200)
    except Exception as e:
        error_msg = f"Failed to fetch clients: {str(e)}"
        logger("CLIENT", error_msg, "GET /clients", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.get("/search/{search_term}", response_model=Dict)
async def search_clients(search_term: str):
    """Search clients by full name - JSON response"""
    try:
        results = ClientFunctions.search_clients_by_full_name(search_term)
        success_msg = f"Searched clients for '{search_term}', found {len(results)} results"
        logger("CLIENT", success_msg, "GET /clients/search/{search_term}", "INFO")
        search_result = {"results": results, "count": len(results)}
        return ResponseSchema.success(search_result, 200)
    except Exception as e:
        error_msg = f"Failed to search clients with term '{search_term}': {str(e)}"
        logger("CLIENT", error_msg, "GET /clients/search/{search_term}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.get("/{identifier}", response_model=ClientResponse)
async def get_client(identifier: str, current_user: UserInDB = Depends(get_client_user)):
    """Fetch a single client by ID (supports both client_id and user_id) - Clients only - JSON response"""
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
async def create_client(client: ClientCreate, current_user: UserInDB = Depends(get_client_user)):
    """Create a new client profile - Clients only - JSON body accepted"""
    try:
        # Generate UUID if not provided
        client_id = client.client_id or str(uuid.uuid4())
        
        # Check if client already exists for this user
        existing = ClientFunctions.get_client_by_user_id(client.user_id)
        if existing:
            error_msg = f"Client profile already exists for user {client.user_id}"
            logger("CLIENT", error_msg, "POST /clients", "WARNING")
            return ResponseSchema.error(error_msg, 400)
        
        new_client = ClientFunctions.create_client(
            client_id=client_id,
            user_id=client.user_id,
            full_name=client.full_name,
            bio=client.bio,
            website_url=client.website_url,
            profile_picture_url=client.profile_picture_url
        )
        
        success_msg = f"Created client {client_id} for user {client.user_id} with full name '{client.full_name}'"
        logger("CLIENT", success_msg, "POST /clients", "INFO")
        return ResponseSchema.success(new_client, 201)
    except Exception as e:
        error_msg = f"Failed to create client: {str(e)}"
        logger("CLIENT", error_msg, "POST /clients", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.put("/{identifier}", response_model=ClientResponse)
async def update_client(identifier: str, client_update: ClientUpdate, current_user: UserInDB = Depends(get_client_user)):
    """Update client information (supports both client_id and user_id) - Clients only"""
    try:
        # Check if client exists and get actual client_id if user_id was provided
        existing = ClientFunctions.get_client_by_id_or_user_id(identifier)
        if not existing:
            error_msg = f"Client {identifier} not found for update"
            logger("CLIENT", error_msg, "PUT /clients/{identifier}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        client_id = existing["client_id"]
        
        update_data = {k: v for k, v in client_update.dict().items() if v is not None}
        updated_client = ClientFunctions.update_client(client_id, update_data)
        
        success_msg = f"Updated client {client_id} with fields: {', '.join(update_data.keys())}"
        logger("CLIENT", success_msg, "PUT /clients/{identifier}", "INFO")
        return ResponseSchema.success(updated_client, 200)
    except Exception as e:
        error_msg = f"Failed to update client {identifier}: {str(e)}"
        logger("CLIENT", error_msg, "PUT /clients/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_router.delete("/{identifier}", status_code=200)
async def delete_client(identifier: str, current_user: UserInDB = Depends(get_client_user)):
    """Delete a client profile (supports both client_id and user_id) - Clients only"""
    try:
        # Check if client exists and get actual client_id if user_id was provided
        existing = ClientFunctions.get_client_by_id_or_user_id(identifier)
        if not existing:
            error_msg = f"Client {identifier} not found for deletion"
            logger("CLIENT", error_msg, "DELETE /clients/{identifier}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        client_id = existing["client_id"]
        ClientFunctions.delete_client(client_id)
        success_msg = f"Client {client_id} deleted successfully"
        logger("CLIENT", success_msg, "DELETE /clients/{identifier}", "INFO")
        return ResponseSchema.success(success_msg, 200)
    except Exception as e:
        error_msg = f"Failed to delete client {identifier}: {str(e)}"
        logger("CLIENT", error_msg, "DELETE /clients/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
