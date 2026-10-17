"""Public profile APIs. Peer claims never enter the OSINT scratchpad."""

from uuid import UUID
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from backend import public_profiles
from backend.private_vault import open_vault

router = APIRouter(prefix="/api/profiles")


class PeerSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    claim_id: UUID
    author_name: str = Field(min_length=1, max_length=100)
    author_url: HttpUrl
    relationship: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=10, max_length=3000)


@router.get("/{profile_id}")
async def profile(profile_id: str):
    value = await public_profiles.get_profile(profile_id)
    if not value:
        raise HTTPException(404, "Committed public profile not found.")
    with open_vault() as vault:
        representation = vault.representation(profile_id)
        value.update(claimed=representation is not None, representation_ready=bool(representation and representation["ready"]))
    return value


@router.get("/{profile_id}/peers")
async def peers(profile_id: str):
    return {"claims": await public_profiles.peer_claims(profile_id)}


@router.post("/{profile_id}/peers", status_code=201)
async def submit_peer(profile_id: str, body: PeerSubmission):
    value = body.model_dump(mode="json")
    if body.author_url.username or body.author_url.password:
        raise HTTPException(422, "Attribution URL must not contain credentials.")
    created = await public_profiles.add_peer_claim(profile_id, value)
    if not created:
        raise HTTPException(404, "Committed public profile not found.")
    if any(created.get(key) != value[key] for key in value):
        raise HTTPException(409, "This submission ID already belongs to another statement.")
    return {**created, "attribution": "self_attributed"}
