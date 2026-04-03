import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional
from functions.schema_model import ClientTrustScoreCreate, ClientTrustScoreUpdate, ClientTrustScoreResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.client_trust_scores.client_trust_score_functions import ClientTrustScoreFunctions

client_trust_score_router = APIRouter(prefix="/client-trust-scores", tags=["Client Trust Scores"])


@client_trust_score_router.get("", response_model=List[ClientTrustScoreResponse])
async def get_all_client_trust_scores(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all client trust scores - Authenticated users only - JSON response"""
    try:
        scores = ClientTrustScoreFunctions.get_all_client_trust_scores(limit=limit)
        success_msg = f"Retrieved {len(scores)} client trust scores" + (f" (limit: {limit})" if limit else "")
        logger("CLIENT_TRUST_SCORE", success_msg, "GET /client-trust-scores", "INFO")
        return ResponseSchema.success(scores, 200)
    except Exception as e:
        error_msg = f"Failed to fetch client trust scores: {str(e)}"
        logger("CLIENT_TRUST_SCORE", error_msg, "GET /client-trust-scores", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_trust_score_router.get("/{client_id}", response_model=ClientTrustScoreResponse)
async def get_client_trust_score(client_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch trust score for a specific client - Authenticated users only - JSON response"""
    try:
        score = ClientTrustScoreFunctions.get_client_trust_score_by_id(client_id)
        if not score:
            error_msg = f"Client trust score for {client_id} not found"
            logger("CLIENT_TRUST_SCORE", error_msg, "GET /client-trust-scores/{client_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved trust score for client {client_id}"
        logger("CLIENT_TRUST_SCORE", success_msg, "GET /client-trust-scores/{client_id}", "INFO")
        return ResponseSchema.success(score, 200)
    except Exception as e:
        error_msg = f"Failed to fetch trust score for client {client_id}: {str(e)}"
        logger("CLIENT_TRUST_SCORE", error_msg, "GET /client-trust-scores/{client_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_trust_score_router.post("", response_model=ClientTrustScoreResponse, status_code=201)
async def create_client_trust_score(score: ClientTrustScoreCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new client trust score - Authenticated users only - JSON body accepted"""
    try:
        new_score = ClientTrustScoreFunctions.create_client_trust_score(
            client_id=score.client_id,
            trust_score=getattr(score, 'trust_score', 0.0)
        )
        
        success_msg = f"Created trust score for client {score.client_id}"
        logger("CLIENT_TRUST_SCORE", success_msg, "POST /client-trust-scores", "INFO")
        return ResponseSchema.success(new_score, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("CLIENT_TRUST_SCORE", error_msg, "POST /client-trust-scores", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create client trust score: {str(e)}"
        logger("CLIENT_TRUST_SCORE", error_msg, "POST /client-trust-scores", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_trust_score_router.put("/{client_id}", response_model=ClientTrustScoreResponse)
async def update_client_trust_score(client_id: str, score_update: ClientTrustScoreUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update client trust score - Authenticated users only"""
    try:
        existing_score = ClientTrustScoreFunctions.get_client_trust_score_by_id(client_id)
        if not existing_score:
            error_msg = f"Client trust score for {client_id} not found"
            logger("CLIENT_TRUST_SCORE", error_msg, "PUT /client-trust-scores/{client_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        update_data = score_update.model_dump(exclude_unset=True)
        updated_score = ClientTrustScoreFunctions.update_client_trust_score(client_id, update_data)
        
        success_msg = f"Updated trust score for client {client_id}"
        logger("CLIENT_TRUST_SCORE", success_msg, "PUT /client-trust-scores/{client_id}", "INFO")
        return ResponseSchema.success(updated_score, 200)
    except Exception as e:
        error_msg = f"Failed to update trust score for client {client_id}: {str(e)}"
        logger("CLIENT_TRUST_SCORE", error_msg, "PUT /client-trust-scores/{client_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@client_trust_score_router.delete("/{client_id}", status_code=200)
async def delete_client_trust_score(client_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a client trust score - Authenticated users only"""
    try:
        existing_score = ClientTrustScoreFunctions.get_client_trust_score_by_id(client_id)
        if not existing_score:
            error_msg = f"Client trust score for {client_id} not found"
            logger("CLIENT_TRUST_SCORE", error_msg, "DELETE /client-trust-scores/{client_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        ClientTrustScoreFunctions.delete_client_trust_score(client_id)
        
        success_msg = f"Deleted trust score for client {client_id}"
        logger("CLIENT_TRUST_SCORE", success_msg, "DELETE /client-trust-scores/{client_id}", "INFO")
        return ResponseSchema.success(None, 200)
    except Exception as e:
        error_msg = f"Failed to delete trust score for client {client_id}: {str(e)}"
        logger("CLIENT_TRUST_SCORE", error_msg, "DELETE /client-trust-scores/{client_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
