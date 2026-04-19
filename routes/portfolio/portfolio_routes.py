import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import PortfolioCreate, PortfolioUpdate, PortfolioResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_freelancer_owns, get_freelancer_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.portfolio.portfolio_functions import PortfolioFunctions
from ai_related.job_matching.embedding_manager import mark_freelancer_dirty

portfolio_router = APIRouter(prefix="/portfolios", tags=["Portfolio"])


@portfolio_router.get("", response_model=List[PortfolioResponse])
async def get_all_portfolios(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all portfolios - Authenticated users only - JSON response"""
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        portfolios = PortfolioFunctions.get_portfolios_by_freelancer_id(freelancer["freelancer_id"])
        success_msg = f"Retrieved {len(portfolios)} portfolios for freelancer {freelancer['freelancer_id']}"
        logger("PORTFOLIO", success_msg, "GET /portfolios", "INFO")
        return ResponseSchema.success(portfolios, 200)
    except Exception as e:
        error_msg = f"Failed to fetch portfolios: {str(e)}"
        logger("PORTFOLIO", error_msg, "GET /portfolios", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@portfolio_router.get("/{portfolio_id}", response_model=PortfolioResponse)
async def get_portfolio(portfolio_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single portfolio by ID - Authenticated users only - JSON response"""
    try:
        portfolio = PortfolioFunctions.get_portfolio_by_id(portfolio_id)
        if not portfolio:
            error_msg = f"Portfolio {portfolio_id} not found"
            logger("PORTFOLIO", error_msg, "GET /portfolios/{portfolio_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved portfolio {portfolio_id}"
        logger("PORTFOLIO", success_msg, "GET /portfolios/{portfolio_id}", "INFO")
        return ResponseSchema.success(portfolio, 200)
    except Exception as e:
        error_msg = f"Failed to fetch portfolio {portfolio_id}: {str(e)}"
        logger("PORTFOLIO", error_msg, "GET /portfolios/{portfolio_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@portfolio_router.get("/freelancer/{freelancer_id}", response_model=List[PortfolioResponse])
async def get_portfolios_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all portfolios for a specific freelancer - Authenticated users only - JSON response"""
    try:
        portfolios = PortfolioFunctions.get_portfolios_by_freelancer_id(freelancer_id)
        success_msg = f"Retrieved {len(portfolios)} portfolios for freelancer {freelancer_id}"
        logger("PORTFOLIO", success_msg, "GET /portfolios/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(portfolios, 200)
    except Exception as e:
        error_msg = f"Failed to fetch portfolios for freelancer {freelancer_id}: {str(e)}"
        logger("PORTFOLIO", error_msg, "GET /portfolios/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@portfolio_router.post("", response_model=PortfolioResponse, status_code=201)
async def create_portfolio(portfolio: PortfolioCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new portfolio - Authenticated users only - JSON body accepted"""
    try:
        portfolio_id = portfolio.portfolio_id or str(uuid.uuid4())
        assert_freelancer_owns(current_user, portfolio.freelancer_id)
        new_portfolio = PortfolioFunctions.create_portfolio(
            freelancer_id=portfolio.freelancer_id,
            project_title=portfolio.project_title,
            project_description=portfolio.project_description,
            project_url=portfolio.project_url,
            completion_date=getattr(portfolio, 'completion_date', None),
            is_auto_generated=getattr(portfolio, 'is_auto_generated', False),
            contract_id=portfolio.contract_id
        )
        
        mark_freelancer_dirty(str(portfolio.freelancer_id))
        success_msg = f"Created portfolio {portfolio_id} for freelancer {portfolio.freelancer_id}"
        logger("PORTFOLIO", success_msg, "POST /portfolios", "INFO")
        return ResponseSchema.success(new_portfolio, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("PORTFOLIO", error_msg, "POST /portfolios", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create portfolio: {str(e)}"
        logger("PORTFOLIO", error_msg, "POST /portfolios", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@portfolio_router.put("/{portfolio_id}", response_model=PortfolioResponse)
async def update_portfolio(portfolio_id: str, portfolio_update: PortfolioUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update portfolio information - Authenticated users only"""
    try:
        existing_portfolio = PortfolioFunctions.get_portfolio_by_id(portfolio_id)
        if not existing_portfolio:
            error_msg = f"Portfolio {portfolio_id} not found"
            logger("PORTFOLIO", error_msg, "PUT /portfolios/{portfolio_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_portfolio["freelancer_id"])
        
        update_data = portfolio_update.model_dump(exclude_unset=True)
        updated_portfolio = PortfolioFunctions.update_portfolio(portfolio_id, update_data)
        
        mark_freelancer_dirty(str(existing_portfolio["freelancer_id"]))
        success_msg = f"Updated portfolio {portfolio_id}"
        logger("PORTFOLIO", success_msg, "PUT /portfolios/{portfolio_id}", "INFO")
        return ResponseSchema.success(updated_portfolio, 200)
    except Exception as e:
        error_msg = f"Failed to update portfolio {portfolio_id}: {str(e)}"
        logger("PORTFOLIO", error_msg, "PUT /portfolios/{portfolio_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@portfolio_router.delete("/{portfolio_id}", status_code=200)
async def delete_portfolio(portfolio_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a portfolio - Authenticated users only"""
    try:
        existing_portfolio = PortfolioFunctions.get_portfolio_by_id(portfolio_id)
        if not existing_portfolio:
            error_msg = f"Portfolio {portfolio_id} not found"
            logger("PORTFOLIO", error_msg, "DELETE /portfolios/{portfolio_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_portfolio["freelancer_id"])
        
        fid = str(existing_portfolio["freelancer_id"])
        PortfolioFunctions.delete_portfolio(portfolio_id)
        mark_freelancer_dirty(fid)
        success_msg = f"Deleted portfolio {portfolio_id}"
        logger("PORTFOLIO", success_msg, "DELETE /portfolios/{portfolio_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete portfolio {portfolio_id}: {str(e)}"
        logger("PORTFOLIO", error_msg, "DELETE /portfolios/{portfolio_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
