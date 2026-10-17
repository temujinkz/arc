"""Visitor representation bridge: has no journal-reading capability."""

import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend import local_agent, public_profiles
from backend.private_vault import open_vault

router = APIRouter(prefix="/api/profiles")
# The API deliberately runs one worker. Serialize duplicate requests across awaits.
_locks: dict[str, asyncio.Lock] = {}


class AgentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    kind: Literal["question", "collaboration"]
    question: str = Field(min_length=3, max_length=3000)


@router.post("/{profile_id}/agent")
async def ask_agent(profile_id: str, body: AgentQuestion):
    profile = await public_profiles.get_profile(profile_id)
    if not profile:
        raise HTTPException(404, "Public profile not found.")
    lock = _locks.setdefault(profile_id, asyncio.Lock())
    async with lock:
        try:
            with open_vault() as vault:
                representation = vault.representation(profile_id)
                if representation is None:
                    raise HTTPException(409, "This profile has not activated a local representative.")
                # Do not replay revoked statements after journal/approval changes.
                previous = vault.reply(profile_id, str(body.request_id), body.kind, body.question)
                if not representation["ready"]:
                    return {"mode": "awaiting_owner", "facts": [], "message": "The owner needs to approve current shareable context before their agent can respond."}
                if previous and previous.get("representation") == representation:
                    return {key: value for key, value in previous.items() if key != "representation"}
            reply = await local_agent.represent(profile, representation, body.question, body.kind)
            with open_vault() as vault:
                if vault.representation(profile_id) != representation:
                    return {"mode": "awaiting_owner", "facts": [], "message": "The owner's context changed. Please ask again after they review it."}
                stored = {**reply, "representation": representation}
                if previous:
                    # An old request is never silently answered using revoked content.
                    raise HTTPException(409, "Owner context changed since this request. Submit a new question.")
                vault.reply(profile_id, str(body.request_id), body.kind, body.question, stored)
            return reply
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
