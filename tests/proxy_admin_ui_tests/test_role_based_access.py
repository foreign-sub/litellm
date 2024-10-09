"""
RBAC tests
"""

import os
import sys
import traceback
import uuid
from datetime import datetime

from dotenv import load_dotenv
from fastapi import Request
from fastapi.routing import APIRoute

load_dotenv()
import io
import os
import time

# this file is to test litellm/proxy

sys.path.insert(
    0, os.path.abspath("../..")
)  # Adds the parent directory to the system path
import asyncio
import logging

import pytest

import litellm
from litellm._logging import verbose_proxy_logger
from litellm.proxy.auth.auth_checks import get_user_object
from litellm.proxy.management_endpoints.key_management_endpoints import (
    delete_key_fn,
    generate_key_fn,
    generate_key_helper_fn,
    info_key_fn,
    regenerate_key_fn,
    update_key_fn,
)
from litellm.proxy.management_endpoints.internal_user_endpoints import new_user

from litellm.proxy.management_endpoints.team_endpoints import (
    new_team,
    team_info,
    update_team,
)
from litellm.proxy.proxy_server import (
    LitellmUserRoles,
    audio_transcriptions,
    chat_completion,
    completion,
    embeddings,
    image_generation,
    model_list,
    moderations,
    new_end_user,
    user_api_key_auth,
    new_organization,
)
from litellm.proxy.spend_tracking.spend_management_endpoints import (
    global_spend,
    global_spend_logs,
    global_spend_models,
    global_spend_keys,
    spend_key_fn,
    spend_user_fn,
    view_spend_logs,
)
from starlette.datastructures import URL

from litellm.proxy.utils import PrismaClient, ProxyLogging, hash_token, update_spend

verbose_proxy_logger.setLevel(level=logging.DEBUG)

from starlette.datastructures import URL

from litellm.caching import DualCache
from litellm.proxy._types import (
    DynamoDBArgs,
    GenerateKeyRequest,
    NewOrganizationRequest,
    RegenerateKeyRequest,
    KeyRequest,
    LiteLLM_UpperboundKeyGenerateParams,
    NewCustomerRequest,
    NewTeamRequest,
    NewUserRequest,
    ProxyErrorTypes,
    ProxyException,
    UpdateKeyRequest,
    UpdateTeamRequest,
    UpdateUserRequest,
    UserAPIKeyAuth,
)

proxy_logging_obj = ProxyLogging(user_api_key_cache=DualCache())


@pytest.fixture
def prisma_client():
    from litellm.proxy.proxy_cli import append_query_params

    ### add connection pool + pool timeout args
    params = {"connection_limit": 100, "pool_timeout": 60}
    database_url = os.getenv("DATABASE_URL")
    modified_url = append_query_params(database_url, params)
    os.environ["DATABASE_URL"] = modified_url

    # Assuming PrismaClient is a class that needs to be instantiated
    prisma_client = PrismaClient(
        database_url=os.environ["DATABASE_URL"], proxy_logging_obj=proxy_logging_obj
    )

    # Reset litellm.proxy.proxy_server.prisma_client to None
    litellm.proxy.proxy_server.litellm_proxy_budget_name = (
        f"litellm-proxy-budget-{time.time()}"
    )
    litellm.proxy.proxy_server.user_custom_key_generate = None

    return prisma_client


"""
RBAC Tests

1. create a new user with an organization id 
    - test 1 - if organization_id does exist expect to create a new user and user, organization relation

2. org admin creates team in his org → success 

3. org admin adds new internal user to his org → success 

4. org admin creates team and internal user not in his org → fail both
"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_role",
    [
        None,
        LitellmUserRoles.PROXY_ADMIN,
        LitellmUserRoles.ORG_ADMIN,
        LitellmUserRoles.INTERNAL_USER,
        LitellmUserRoles.INTERNAL_USER_VIEW_ONLY,
    ],
)
async def test_create_new_user_in_organization(prisma_client, user_role):
    """
    Create a new user in an organization and assert the user object is created with the correct organization memberships / roles
    """
    master_key = "sk-1234"
    setattr(litellm.proxy.proxy_server, "prisma_client", prisma_client)
    setattr(litellm.proxy.proxy_server, "master_key", master_key)

    await litellm.proxy.proxy_server.prisma_client.connect()

    response = await new_organization(
        data=NewOrganizationRequest(
            organization_alias=f"new-org-{uuid.uuid4()}",
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_role=LitellmUserRoles.PROXY_ADMIN,
        ),
    )

    org_id = response.organization_id

    response = await new_user(
        data=NewUserRequest(organization_id=org_id, user_role=user_role)
    )

    print("new user response", response)

    # call get_user_object

    user_object = await get_user_object(
        user_id=response.user_id,  # type: ignore
        prisma_client=prisma_client,
        user_api_key_cache=DualCache(),
        user_id_upsert=False,
    )

    print("user object", user_object)

    assert user_object.organization_memberships is not None

    _membership = user_object.organization_memberships[0]

    assert _membership.user_id == response.user_id
    assert _membership.organization_id == org_id

    if user_role != None:
        assert _membership.user_role == user_role
    else:
        assert _membership.user_role == LitellmUserRoles.INTERNAL_USER_VIEW_ONLY


@pytest.mark.asyncio
async def test_org_admin_create_team_permissions(prisma_client):
    """
    Create a new org admin

    org admin creates a new team in their org -> success
    """
    import json

    master_key = "sk-1234"
    setattr(litellm.proxy.proxy_server, "prisma_client", prisma_client)
    setattr(litellm.proxy.proxy_server, "master_key", master_key)

    await litellm.proxy.proxy_server.prisma_client.connect()

    response = await new_organization(
        data=NewOrganizationRequest(
            organization_alias=f"new-org-{uuid.uuid4()}",
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_role=LitellmUserRoles.PROXY_ADMIN,
        ),
    )

    org_id = response.organization_id

    response = await new_user(
        data=NewUserRequest(
            organization_id=org_id, user_role=LitellmUserRoles.ORG_ADMIN
        )
    )

    # create key with the response["user_id"]

    _new_key = await generate_key_fn(
        data=GenerateKeyRequest(
            user_id=response.user_id,
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_role=LitellmUserRoles.ORG_ADMIN,
            user_id=response.user_id,
        ),
    )

    new_key = _new_key.key

    print("user api key auth response", response)

    # Create /team/new request -> expect auth to pass
    request = Request(scope={"type": "http"})
    request._url = URL(url="/team/new")

    async def return_body():
        body = {"organization_id": org_id}
        return bytes(json.dumps(body), "utf-8")

    request.body = return_body
    response = await user_api_key_auth(request=request, api_key="Bearer " + new_key)

    # after auth - actually create team now
    response = await new_team(
        data=NewTeamRequest(
            organization_id=org_id,
        ),
        http_request=request,
        user_api_key_dict=UserAPIKeyAuth(
            user_id=response.user_id,
            user_role=LitellmUserRoles.ORG_ADMIN,
        ),
    )

    print("response from new team")


@pytest.mark.asyncio
async def test_org_admin_create_user_permissions(prisma_client):
    """
    Create a new org admin

    org admin creates a new user in their org -> success
    """
    import json

    master_key = "sk-1234"
    setattr(litellm.proxy.proxy_server, "prisma_client", prisma_client)
    setattr(litellm.proxy.proxy_server, "master_key", master_key)

    await litellm.proxy.proxy_server.prisma_client.connect()

    response = await new_organization(
        data=NewOrganizationRequest(
            organization_alias=f"new-org-{uuid.uuid4()}",
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_role=LitellmUserRoles.PROXY_ADMIN,
        ),
    )

    org_id = response.organization_id

    response = await new_user(
        data=NewUserRequest(
            organization_id=org_id, user_role=LitellmUserRoles.ORG_ADMIN
        )
    )

    # create key with the response["user_id"]

    _new_key = await generate_key_fn(
        data=GenerateKeyRequest(
            user_id=response.user_id,
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_role=LitellmUserRoles.ORG_ADMIN,
            user_id=response.user_id,
        ),
    )

    new_key = _new_key.key

    print("user api key auth response", response)

    # Create /user/new request -> expect auth to pass
    request = Request(scope={"type": "http"})
    request._url = URL(url="/user/new")

    async def return_body():
        body = {"organization_id": org_id}
        return bytes(json.dumps(body), "utf-8")

    request.body = return_body
    response = await user_api_key_auth(request=request, api_key="Bearer " + new_key)

    # after auth - actually create team now
    response = await new_user(
        data=NewUserRequest(
            organization_id=org_id,
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_id=response.user_id,
            user_role=LitellmUserRoles.ORG_ADMIN,
        ),
    )

    print("response from new team")


@pytest.mark.asyncio
async def test_org_admin_create_user_team_wrong_org_permissions(prisma_client):
    """
    Create a new org admin

    org admin creates a new user and new team in orgs they are not part of -> expect error
    """
    import json

    master_key = "sk-1234"
    setattr(litellm.proxy.proxy_server, "prisma_client", prisma_client)
    setattr(litellm.proxy.proxy_server, "master_key", master_key)

    await litellm.proxy.proxy_server.prisma_client.connect()

    response = await new_organization(
        data=NewOrganizationRequest(
            organization_alias=f"new-org-{uuid.uuid4()}",
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_role=LitellmUserRoles.PROXY_ADMIN,
        ),
    )

    response2 = await new_organization(
        data=NewOrganizationRequest(
            organization_alias=f"new-org-{uuid.uuid4()}",
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_role=LitellmUserRoles.PROXY_ADMIN,
        ),
    )

    org_id = response.organization_id

    org_without_admins = response2.organization_id

    response = await new_user(
        data=NewUserRequest(
            organization_id=org_id, user_role=LitellmUserRoles.ORG_ADMIN
        )
    )

    # create key with the response["user_id"]

    _new_key = await generate_key_fn(
        data=GenerateKeyRequest(
            user_id=response.user_id,
        ),
        user_api_key_dict=UserAPIKeyAuth(
            user_role=LitellmUserRoles.ORG_ADMIN,
            user_id=response.user_id,
        ),
    )

    new_key = _new_key.key

    print("user api key auth response", response)

    # Create /user/new request in organization=org_without_admins -> expect fail
    request = Request(scope={"type": "http"})
    request._url = URL(url="/user/new")

    async def return_body():
        body = {"organization_id": org_without_admins}
        return bytes(json.dumps(body), "utf-8")

    request.body = return_body

    try:
        response = await user_api_key_auth(request=request, api_key="Bearer " + new_key)
        pytest.fail(
            f"This should have failed!. creating a user in an org without admins"
        )
    except Exception as e:
        print("got exception", e)
        print("exception.message", e.message)
        assert (
            "You are not a member of the organization specified in the request body. Passed organization_id"
            in e.message
        )

    # Create /team/new request in organization=org_without_admins -> expect fail
    request = Request(scope={"type": "http"})
    request._url = URL(url="/team/new")

    async def return_body():
        body = {"organization_id": org_without_admins}
        return bytes(json.dumps(body), "utf-8")

    request.body = return_body

    try:
        response = await user_api_key_auth(request=request, api_key="Bearer " + new_key)
        pytest.fail(
            f"This should have failed!. creating a user in an org without admins"
        )
    except Exception as e:
        print("got exception", e)
        print("exception.message", e.message)
        assert (
            "You are not a member of the organization specified in the request body"
            in e.message
        )
