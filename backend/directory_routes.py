"""People directory routes. Local use only: they spend money and return contact details."""

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend import directory

router = APIRouter(prefix="/api/directory")
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def local_only(request: Request):
    """Paid and private, so only this machine may call it. A page on another site sends its own Origin and is refused."""
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS and host != "testclient":
        raise HTTPException(403, "The people directory is only available on this machine.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in LOCAL_HOSTS:
        raise HTTPException(403, "The people directory is only available from the local site.")


class Who(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=3, max_length=120)
    company: str | None = Field(default=None, max_length=120)


class Lookup(Who):
    refresh: bool = False  # search again even if the answer is remembered


class Reveal(Who):
    linkedin_url: str | None = Field(default=None, max_length=300)
    phone: bool = False
    confirm: bool = False  # the caller has shown the price and the user said go ahead


def fail(error: directory.DirectoryError):
    raise HTTPException(error.status, str(error)) from None


@router.get("/status")
async def status(request: Request):
    local_only(request)
    try:
        with_key = bool(directory.api_key())
    except directory.DirectoryError:
        with_key = False
    store = directory.open_store()
    try:
        spent = store.spent()
    finally:
        store.close()
    try:
        cap = directory.spend_cap()
    except directory.DirectoryError as error:
        fail(error)
    return {"configured": with_key, "spent_usd": float(spent), "cap_usd": float(cap),
            "price_usd": {"profile_and_email": float(directory.EMAIL_COST), "phone_extra": float(directory.PHONE_COST)}}


@router.post("/lookup")
async def lookup(body: Lookup, request: Request):
    local_only(request)
    store = directory.open_store()
    try:
        async with directory.http_client() as client:
            return await directory.lookup(store, client, body.name, body.company, body.refresh)
    except directory.DirectoryError as error:
        fail(error)
    finally:
        store.close()


@router.get("/person")
async def person(name: str, request: Request, company: str | None = None):
    local_only(request)
    store = directory.open_store()
    try:
        return directory.view(store, directory.person_key(name, company))
    finally:
        store.close()


@router.post("/reveal")
async def reveal(body: Reveal, request: Request):
    local_only(request)
    if not body.confirm:
        raise HTTPException(422, f"This costs money. Send confirm=true after showing ${directory.estimate(body.phone)}.")
    store = directory.open_store()
    try:
        async with directory.http_client() as client:
            return await directory.reveal(store, client, body.name, body.company, linkedin_url=body.linkedin_url, phone=body.phone)
    except directory.DirectoryError as error:
        fail(error)
    finally:
        store.close()


@router.get("/phone/{run_id}")
async def phone(run_id: str, request: Request):
    local_only(request)
    store = directory.open_store()
    try:
        async with directory.http_client() as client:
            return await directory.poll_phone(store, client, run_id)
    except directory.DirectoryError as error:
        fail(error)
    finally:
        store.close()
