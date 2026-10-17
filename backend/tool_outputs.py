"""Normalize MCP output and recover SDK-persisted results without provider calls."""

import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID


def source_cache_info(response: Any) -> dict | None:
    if not isinstance(response, dict):
        return None
    metadata = (response.get("_meta") or {}).get("arc_source_cache")
    if isinstance(metadata, dict):
        return metadata
    content = response.get("content", [])
    if isinstance(content, list) and content and isinstance(content[-1], dict):
        text = content[-1].get("text", "")
        if text.startswith("ARC_SOURCE_CACHE "):
            try:
                value = json.loads(text.removeprefix("ARC_SOURCE_CACHE "))
                if isinstance(value, dict) and isinstance(value.get("hit"), bool) and value.get("observed_at"):
                    return value
            except ValueError:
                pass
    return None


def normalize_output(response: Any) -> Any:
    data = response
    if isinstance(data, dict) and not is_error(data):
        structured = data.get("structuredContent")
        data = structured if structured is not None else data.get("content", data)
    if source_cache_info(response) and isinstance(data, list) and data and isinstance(data[-1], dict):
        if data[-1].get("text", "").startswith("ARC_SOURCE_CACHE "):
            data = data[:-1]
    if isinstance(data, list) and data and all(
        isinstance(block, dict) and block.get("type") == "text" for block in data
    ):
        data = "\n".join(block.get("text", "") for block in data)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            pass
    # FastMCP wraps list/scalar results in its structuredContent.result field.
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return data


def is_error(data: Any) -> bool:
    if isinstance(data, dict):
        return bool(data.get("isError") or data.get("is_error")
                    or data.get("error") or data.get("success") is False)
    if isinstance(data, list) and data:
        return all(is_error(item) for item in data)
    return False


def persisted_output_path(data: Any) -> str | None:
    """Recognize SDK transport notices, not arbitrary paths in source documents."""
    if not isinstance(data, str):
        return None
    patterns = (
        r"\AError: result \([\d,]+ characters\) exceeds maximum allowed tokens\. "
        r"Output has been saved to ([^\r\n]+)\.(?:\r?\n|$)",
        r"\A<persisted-output>\s*Output too large[^\r\n]*\r?\n"
        r"Full output saved to: ([^\r\n]+)",
    )
    for pattern in patterns:
        if match := re.match(pattern, data):
            return match[1]
    return None


def recover_persisted_output(response: Any, hook: dict, *, max_bytes: int) -> tuple[Any, bool]:
    data = normalize_output(response)
    path = persisted_output_path(data)
    if path is None or is_error(response):
        return response, False
    # The path originates in tool output, so it cannot authorize arbitrary file
    # reads. Only this SDK session's tool-results directory is eligible.
    session_id = str(UUID(hook["session_id"]))
    projects = (Path(os.getenv("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects").resolve()
    transcript = Path(hook["transcript_path"]).resolve(strict=True)
    relative = transcript.relative_to(projects)
    if len(relative.parts) < 2:
        raise ValueError("Invalid SDK transcript location.")
    project = projects / relative.parts[0]
    session = project / session_id
    if transcript != project / f"{session_id}.jsonl" and not transcript.is_relative_to(session):
        raise ValueError("Transcript does not belong to this SDK session.")
    allowed = (session / "tool-results").resolve(strict=True)
    if not allowed.is_relative_to(projects):
        raise ValueError("SDK output directory escapes the configured projects directory.")
    file = Path(path).resolve(strict=True)
    if file.parent != allowed or file.suffix != ".txt" or not file.name.startswith("mcp-"):
        raise ValueError("Output is not a saved MCP result from this SDK session.")
    with file.open("rb") as stream:
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("Saved result exceeds the scratchpad evidence allowance.")
    text = content.decode("utf-8")
    try:
        return json.loads(text), True
    except ValueError:
        return text, True


PROFILE_FIELDS = {
    "linkedinUrl", "publicIdentifier", "firstName", "lastName", "headline", "about",
    "industry", "topSkills", "experience", "education", "certifications", "projects",
    "volunteering", "skills", "publications", "courses", "patents", "honorsAndAwards",
    "languages", "organizations", "featured",
}
PROFILE_MEDIA_FIELDS = {
    "companyLogo", "schoolLogo", "issuedByLogo", "companyId", "schoolId", "objectUrn",
    "id", "photo", "image", "images", "thumbnail", "picture", "logo", "backgroundImage",
    "author", "profilePicture", "coverPicture",
}


def model_output(tool_name: str, response: Any) -> Any:
    """A professional-field view; preserve descriptions, dates and source links.

    This omits related people, images, tracking/locale metadata and contact fields.
    The original remains in scratchpad evidence. No text or list slicing occurs.
    """
    data = normalize_output(response)
    if tool_name != "mcp__osint__scrape_linkedin_profile" or not isinstance(data, dict) or is_error(data):
        return data

    def without_media(value):
        if isinstance(value, list):
            return [without_media(item) for item in value]
        if isinstance(value, dict):
            return {key: without_media(item) for key, item in value.items() if key not in PROFILE_MEDIA_FIELDS}
        return value

    return {"profile": without_media({key: value for key, value in data.items() if key in PROFILE_FIELDS}),
            "view": "Professional fields; original retained in ARC evidence. Descriptions and lists are not truncated.",
            "omitted_fields": sorted(set(data) - PROFILE_FIELDS)}
