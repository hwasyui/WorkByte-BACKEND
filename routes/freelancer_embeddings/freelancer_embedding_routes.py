import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional
from functions.schema_model import FreelancerEmbeddingCreate, FreelancerEmbeddingUpdate, FreelancerEmbeddingResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.freelancer_embeddings.freelancer_embedding_functions import FreelancerEmbeddingFunctions

freelancer_embedding_router = APIRouter(prefix="/freelancer-embeddings", tags=["Freelancer Embeddings"])


@freelancer_embedding_router.get("", response_model=List[FreelancerEmbeddingResponse])
async def get_all_freelancer_embeddings(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all freelancer embeddings - Authenticated users only - JSON response"""
    try:
        embeddings = FreelancerEmbeddingFunctions.get_all_freelancer_embeddings(limit=limit)
        success_msg = f"Retrieved {len(embeddings)} freelancer embeddings" + (f" (limit: {limit})" if limit else "")
        logger("FREELANCER_EMBEDDING", success_msg, "GET /freelancer-embeddings", "INFO")
        return ResponseSchema.success(embeddings, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancer embeddings: {str(e)}"
        logger("FREELANCER_EMBEDDING", error_msg, "GET /freelancer-embeddings", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_embedding_router.get("/{embedding_id}", response_model=FreelancerEmbeddingResponse)
async def get_freelancer_embedding(embedding_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single freelancer embedding by ID - Authenticated users only - JSON response"""
    try:
        embedding = FreelancerEmbeddingFunctions.get_freelancer_embedding_by_id(embedding_id)
        if not embedding:
            error_msg = f"Freelancer embedding {embedding_id} not found"
            logger("FREELANCER_EMBEDDING", error_msg, "GET /freelancer-embeddings/{embedding_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved freelancer embedding {embedding_id}"
        logger("FREELANCER_EMBEDDING", success_msg, "GET /freelancer-embeddings/{embedding_id}", "INFO")
        return ResponseSchema.success(embedding, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancer embedding {embedding_id}: {str(e)}"
        logger("FREELANCER_EMBEDDING", error_msg, "GET /freelancer-embeddings/{embedding_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_embedding_router.get("/freelancer/{freelancer_id}", response_model=FreelancerEmbeddingResponse)
async def get_freelancer_embedding_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch embedding for a specific freelancer - Authenticated users only - JSON response"""
    try:
        embedding = FreelancerEmbeddingFunctions.get_freelancer_embedding_by_freelancer_id(freelancer_id)
        if not embedding:
            error_msg = f"Freelancer embedding for freelancer {freelancer_id} not found"
            logger("FREELANCER_EMBEDDING", error_msg, "GET /freelancer-embeddings/freelancer/{freelancer_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved embedding for freelancer {freelancer_id}"
        logger("FREELANCER_EMBEDDING", success_msg, "GET /freelancer-embeddings/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(embedding, 200)
    except Exception as e:
        error_msg = f"Failed to fetch embedding for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER_EMBEDDING", error_msg, "GET /freelancer-embeddings/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_embedding_router.post("", response_model=FreelancerEmbeddingResponse, status_code=201)
async def create_freelancer_embedding(embedding: FreelancerEmbeddingCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new freelancer embedding - Authenticated users only - JSON body accepted"""
    try:
        new_embedding = FreelancerEmbeddingFunctions.create_freelancer_embedding(
            freelancer_id=embedding.freelancer_id,
            embedding_vector=embedding.embedding_vector,
            source_text=getattr(embedding, 'source_text', None),
            embedding_metadata=getattr(embedding, 'embedding_metadata', None)
        )
        
        success_msg = f"Created freelancer embedding for freelancer {embedding.freelancer_id}"
        logger("FREELANCER_EMBEDDING", success_msg, "POST /freelancer-embeddings", "INFO")
        return ResponseSchema.success(new_embedding, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("FREELANCER_EMBEDDING", error_msg, "POST /freelancer-embeddings", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create freelancer embedding: {str(e)}"
        logger("FREELANCER_EMBEDDING", error_msg, "POST /freelancer-embeddings", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_embedding_router.put("/{embedding_id}", response_model=FreelancerEmbeddingResponse)
async def update_freelancer_embedding(embedding_id: str, embedding_update: FreelancerEmbeddingUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update freelancer embedding information - Authenticated users only"""
    try:
        existing_embedding = FreelancerEmbeddingFunctions.get_freelancer_embedding_by_id(embedding_id)
        if not existing_embedding:
            error_msg = f"Freelancer embedding {embedding_id} not found"
            logger("FREELANCER_EMBEDDING", error_msg, "PUT /freelancer-embeddings/{embedding_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        update_data = embedding_update.model_dump(exclude_unset=True)
        updated_embedding = FreelancerEmbeddingFunctions.update_freelancer_embedding(embedding_id, update_data)
        
        success_msg = f"Updated freelancer embedding {embedding_id}"
        logger("FREELANCER_EMBEDDING", success_msg, "PUT /freelancer-embeddings/{embedding_id}", "INFO")
        return ResponseSchema.success(updated_embedding, 200)
    except Exception as e:
        error_msg = f"Failed to update freelancer embedding {embedding_id}: {str(e)}"
        logger("FREELANCER_EMBEDDING", error_msg, "PUT /freelancer-embeddings/{embedding_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_embedding_router.delete("/{embedding_id}", status_code=200)
async def delete_freelancer_embedding(embedding_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a freelancer embedding - Authenticated users only"""
    try:
        existing_embedding = FreelancerEmbeddingFunctions.get_freelancer_embedding_by_id(embedding_id)
        if not existing_embedding:
            error_msg = f"Freelancer embedding {embedding_id} not found"
            logger("FREELANCER_EMBEDDING", error_msg, "DELETE /freelancer-embeddings/{embedding_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        FreelancerEmbeddingFunctions.delete_freelancer_embedding(embedding_id)
        
        success_msg = f"Deleted freelancer embedding {embedding_id}"
        logger("FREELANCER_EMBEDDING", success_msg, "DELETE /freelancer-embeddings/{embedding_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete freelancer embedding {embedding_id}: {str(e)}"
        logger("FREELANCER_EMBEDDING", error_msg, "DELETE /freelancer-embeddings/{embedding_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
