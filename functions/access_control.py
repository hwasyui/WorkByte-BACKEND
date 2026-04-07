import os
import sys
from fastapi import HTTPException, status

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.clients.client_functions import ClientFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions


def _forbidden(message: str):
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def _not_found(message: str):
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


def get_client_profile_for_user(current_user):
    if current_user.type != "client":
        _forbidden("Only clients can use this resource")
    client = ClientFunctions.get_client_by_user_id(current_user.user_id)
    if not client:
        _forbidden("Client profile not found for the current user")
    return client


def get_freelancer_profile_for_user(current_user):
    if current_user.type != "freelancer":
        _forbidden("Only freelancers can use this resource")
    freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
    if not freelancer:
        _forbidden("Freelancer profile not found for the current user")
    return freelancer


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
    if current_user.type == "client":
        client = get_client_profile_for_user(current_user)
        if str(contract.get("client_id")) != str(client["client_id"]):
            _forbidden("Cannot access contracts that do not belong to this client")
    elif current_user.type == "freelancer":
        freelancer = get_freelancer_profile_for_user(current_user)
        if str(contract.get("freelancer_id")) != str(freelancer["freelancer_id"]):
            _forbidden("Cannot access contracts that do not belong to this freelancer")
    else:
        _forbidden("Cannot access contract data")
