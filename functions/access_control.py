import os
import sys
from fastapi import HTTPException, status

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.clients.client_functions import ClientFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from functions.db_manager import get_db


def _forbidden(message: str):
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def _not_found(message: str):
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


def get_client_profile_for_user(current_user):
    if not current_user.client_id:
        _forbidden("A client profile is required to use this resource")
    client = ClientFunctions.get_client_by_user_id(current_user.user_id)
    if not client:
        _forbidden("Client profile not found for the current user")
    return client


def get_freelancer_profile_for_user(current_user):
    if not current_user.freelancer_id:
        _forbidden("A freelancer profile is required to use this resource")
    freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
    if not freelancer:
        _forbidden("Freelancer profile not found for the current user")
    return freelancer


def assert_client_profile_complete(client: dict):
    """Server-side mirror of Flutter's isProfileComplete check for clients
    (profile_provider.dart) - closes the gap where the completeness prompt was
    only ever a UI nudge, bypassable by calling the API directly."""
    missing = []
    if not (client.get("full_name") or "").strip():
        missing.append("full name")
    if not (client.get("bio") or "").strip():
        missing.append("bio")
    if missing:
        _forbidden(f"Please complete your profile before posting a job. Missing: {', '.join(missing)}.")


def assert_freelancer_profile_complete(freelancer: dict):
    """Server-side mirror of Flutter's isProfileComplete check for freelancers
    (profile_provider.dart) - same rationale as assert_client_profile_complete."""
    missing = []
    if not (freelancer.get("full_name") or "").strip():
        missing.append("full name")
    if not (freelancer.get("bio") or "").strip():
        missing.append("bio")
    if not (freelancer.get("cv_file_url") or "").strip():
        missing.append("CV")

    freelancer_id = freelancer["freelancer_id"]
    db = get_db()
    if not db.execute_query("SELECT 1 FROM education WHERE freelancer_id = :fid LIMIT 1", {"fid": freelancer_id}):
        missing.append("education")
    if not db.execute_query("SELECT 1 FROM work_experience WHERE freelancer_id = :fid LIMIT 1", {"fid": freelancer_id}):
        missing.append("work experience")
    if not db.execute_query("SELECT 1 FROM freelancer_skill WHERE freelancer_id = :fid LIMIT 1", {"fid": freelancer_id}):
        missing.append("skills")

    if missing:
        _forbidden(f"Please complete your profile before submitting a proposal. Missing: {', '.join(missing)}.")


def assert_user_owns(current_user, user_id: str):
    if str(current_user.user_id) != str(user_id):
        _forbidden("Cannot access another user's data")


def assert_client_owns(current_user, client_id: str):
    client = get_client_profile_for_user(current_user)
    if str(client_id) != str(client["client_id"]):
        _forbidden("Cannot access another client's data")


def assert_freelancer_owns(current_user, freelancer_id: str):
    freelancer = get_freelancer_profile_for_user(current_user)
    if str(freelancer_id) != str(freelancer["freelancer_id"]):
        _forbidden("Cannot access another freelancer's data")


def assert_current_user_is_contract_party(current_user, contract):
    if not contract:
        _not_found("Contract not found")
    if current_user.client_id:
        client = ClientFunctions.get_client_by_user_id(current_user.user_id)
        if client and str(contract.get("client_id")) == str(client["client_id"]):
            return
    if current_user.freelancer_id:
        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if freelancer and str(contract.get("freelancer_id")) == str(freelancer["freelancer_id"]):
            return
    _forbidden("Cannot access contracts that do not belong to you")
