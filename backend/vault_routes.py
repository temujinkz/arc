"""Owner-only local journal endpoints; no raw notes in SSE or public storage."""

import ipaddress
import os
import secrets
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.private_vault import open_vault, OwnershipError
from backend.public_profiles import get_profile
from backend.local_agent import prepare_brief

router = APIRouter(prefix="/api/vault")
COOKIE = "arc_local_owner"


def local_request(request: Request):
    origins = {origin.strip() for origin in os.getenv("ARC_FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")}
    try:
        local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local or request.headers.get("origin") not in origins:
        raise HTTPException(403, "Private journal access requires the local ARC browser.")


def owner_token(request: Request) -> str:
    local_request(request)
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Claim this profile on this device to open its private journal.")
    return token


@router.post("/{profile_id}/claim")
async def claim_profile(profile_id: str, request: Request, response: Response):
    local_request(request)
    if not await get_profile(profile_id):
        raise HTTPException(404, "Committed public profile not found.")
    token = request.cookies.get(COOKIE) or secrets.token_urlsafe(32)
    try:
        with open_vault() as vault:
            vault.claim(profile_id, token)
    except OwnershipError as exc:
        raise HTTPException(409, str(exc)) from None
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/api/vault",
                        max_age=365 * 24 * 60 * 60, secure=request.url.scheme == "https")
    response.headers["Cache-Control"] = "no-store"
    return {"claimed": True, "claim_kind": "local_self_claim", "identity_verified": False}


@router.get("/{profile_id}")
async def journal(profile_id: str, request: Request, response: Response):
    token = owner_token(request)
    try:
        with open_vault() as vault:
            view = vault.owner_view(profile_id, token)
    except OwnershipError as exc:
        raise HTTPException(403, str(exc)) from None
    response.headers["Cache-Control"] = "no-store"
    return view


class JournalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    entry_id: UUID
    kind: Literal["context", "goal", "note"]
    text: str = Field(min_length=1, max_length=12000)


@router.post("/{profile_id}/entries", status_code=201)
async def add_entry(profile_id: str, body: JournalEntry, request: Request):
    token = owner_token(request)
    try:
        with open_vault() as vault:
            vault.add_entry(profile_id, token, str(body.entry_id), body.kind, body.text)
    except OwnershipError as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"saved_locally": True}


@router.delete("/{profile_id}/entries/{entry_id}")
async def delete_entry(profile_id: str, entry_id: UUID, request: Request):
    token = owner_token(request)
    try:
        with open_vault() as vault:
            vault.delete_entry(profile_id, token, str(entry_id))
    except OwnershipError as exc:
        raise HTTPException(403, str(exc)) from None
    return {"deleted": True}


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=0)
    statements: list[str] = Field(max_length=20)


@router.post("/{profile_id}/prepare")
async def prepare_representation(profile_id: str, request: Request, response: Response):
    token = owner_token(request)
    try:
        with open_vault() as vault:
            view = vault.owner_view(profile_id, token)
    except OwnershipError as exc:
        raise HTTPException(403, str(exc)) from None
    profile = await get_profile(profile_id)
    if not profile:
        raise HTTPException(404, "Public profile not found.")
    response.headers["Cache-Control"] = "no-store"
    result = await prepare_brief(profile, view["entries"])
    with open_vault() as vault:
        if vault.owner_view(profile_id, token)["revision"] != view["revision"]:
            raise HTTPException(409, "Journal changed while drafting. Prepare a new draft.")
    return result


@router.post("/{profile_id}/representation")
async def approve_representation(profile_id: str, body: Approval, request: Request):
    token = owner_token(request)
    statements = [text.strip() for text in body.statements]
    if any(not text or len(text) > 1000 for text in statements):
        raise HTTPException(422, "Use nonblank shareable statements of at most 1,000 characters each.")
    try:
        with open_vault() as vault:
            vault.approve(profile_id, token, body.revision, statements)
    except OwnershipError as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"approved": True}
