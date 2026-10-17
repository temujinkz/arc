"""Free OSINT adapters. Commands are fixed, isolated and never inherit API keys."""

import asyncio
import csv
import io
import json
import os
import re
import shutil
import signal
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode, urlsplit

from fastmcp.exceptions import ToolError

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLES = {"sherlock": "sherlock", "maigret": "maigret", "theharvester": "theHarvester"}
# Match the upstream site databases; scanning unrelated personal sites is out of scope.
PROFESSIONAL_SITES = {"GitHub", "GitLab", "BitBucket", "Docker Hub", "Hugging Face", "Kaggle", "PyPi", "npm"}


def executable(tool: str) -> str | None:
    configured = os.getenv(f"ARC_{tool.upper()}_BIN", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path.resolve()) if path.is_absolute() and path.is_file() and os.access(path, os.X_OK) else None
    local = ROOT / ".arc/bin" / EXECUTABLES[tool]
    return str(local) if local.is_file() and os.access(local, os.X_OK) else shutil.which(EXECUTABLES[tool])


def username_and_sites(username: str, sites: list[str]) -> tuple[str, list[str]]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", username):
        raise ToolError("Provide a literal username from a source or the user, without URLs or wildcards.")
    if not sites or not set(sites) <= PROFESSIONAL_SITES:
        raise ToolError(f"Choose explicit professional sites from: {', '.join(sorted(PROFESSIONAL_SITES))}.")
    return username, list(dict.fromkeys(sites))


async def run_report(tool: str, arguments: list[str], report_name: str) -> str:
    command = executable(tool)
    if not command:
        raise ToolError(f"{tool} is not installed. See README's free OSINT setup instructions.")
    with TemporaryDirectory(prefix=f"arc-{tool}-") as directory:
        # Deliberately exclude HOME/configuration and every cloud credential.
        env = {"PATH": os.defpath, "HOME": directory, "XDG_CONFIG_HOME": directory,
               "XDG_DATA_HOME": directory, "LANG": "C.UTF-8", "NO_COLOR": "1"}
        process = await asyncio.create_subprocess_exec(
            command, *arguments, cwd=directory, env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await process.wait()
        except BaseException:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await process.wait()
            raise
        if process.returncode:
            raise ToolError(f"{tool} exited with status {process.returncode}. Inspect installation/source availability; do not automatically repeat the scan.")
        path = Path(directory) / report_name
        if not path.is_file():
            raise ToolError(f"{tool} did not produce its expected report. Check the installed version; no results were captured.")
        # Same allowance as ARC's existing evidence scratchpad; refuse, never truncate.
        if path.stat().st_size > 500_000:
            raise ToolError("OSINT report exceeds ARC's evidence allowance. Narrow the scope before another scan.")
        return path.read_text(encoding="utf-8")


def public_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return url.scheme in {"https", "http"} and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        return False


def professional_domain(domain: str) -> str:
    domain = domain.strip().lower().rstrip(".")
    if (len(domain) > 253 or any(len(label) > 63 for label in domain.split("."))
            or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", domain)
            or domain.endswith((".local", ".localhost", ".onion", ".internal", ".test", ".invalid", ".example"))):
        raise ToolError("Provide a public professional domain, without a URL, IP address or wildcard.")
    return domain


async def search_username_sherlock(username: str, sites: list[str]) -> dict:
    """Check a sourced username on explicit professional sites; matches are unverified leads.

    sites must use upstream names (e.g. GitHub, GitLab, Kaggle). Do not infer a
    person's identity from matching handles or repeat a successful Maigret scan.
    """
    username, sites = username_and_sites(username, sites)
    args = [username, "--csv", "--local", "--no-color"]
    for site in sites:
        args.extend(["--site", site])
    report = await run_report("sherlock", args, f"{username}.csv")
    try:
        reader = csv.DictReader(io.StringIO(report))
        if not {"name", "url_user", "exists"} <= set(reader.fieldnames or []):
            raise ValueError
        matches = [{"site": row["name"], "url": row["url_user"], "identity_verified": False}
                   for row in reader if row["exists"] == "Claimed" and row["name"] in sites and public_url(row["url_user"])]
    except (ValueError, KeyError, csv.Error):
        raise ToolError("Sherlock returned an unsupported report format; no evidence was accepted.") from None
    return {"username": username, "requested_sites": sites, "matches": matches,
            "limitation": "Presence checks can be wrong. Shared handles do not establish identity; an empty result does not establish absence."}


async def search_username_maigret(username: str, sites: list[str]) -> dict:
    """Alternative username checker on explicit professional sites, with recursion disabled.

    Matches are leads, never verified identities. Use instead of Sherlock unless
    a second check would resolve a specific uncertainty. Does not use Maigret AI.
    """
    username, sites = username_and_sites(username, sites)
    args = [username, "--json", "simple", "--folderoutput", ".", "--no-recursion", "--no-extracting"]
    upstream_names = {"Hugging Face": "HuggingFace", "npm": "NPM"}
    for site in sites:
        args.extend(["--site", upstream_names.get(site, site)])
    report = await run_report("maigret", args, f"report_{username}_simple.json")
    try:
        data = json.loads(report)
        if not isinstance(data, dict):
            raise ValueError
        names = {upstream_names.get(site, site): site for site in sites}
        matches = [{"site": names[site], "url": item["url_user"], "identity_verified": False}
                   for site, item in data.items() if site in names and isinstance(item, dict)
                   and item.get("status", {}).get("status") == "Claimed" and public_url(item.get("url_user", ""))]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ToolError("Maigret returned an unsupported report format; no evidence was accepted.") from None
    return {"username": username, "requested_sites": sites, "matches": matches,
            "limitation": "Presence checks can be wrong. Shared handles do not establish identity; an empty result does not establish absence."}


async def inspect_domain_theharvester(domain: str, result_limit: int) -> dict:
    """Collect public certificate hostnames using theHarvester's free crtsh source only.

    Use a professional domain explicitly found in evidence. No API keys, email
    harvesting, active scans, DNS brute force, breach sources or paid providers.
    result_limit is the caller's requested bound, not a guarantee crt.sh honors it.
    """
    domain = professional_domain(domain)
    if isinstance(result_limit, bool) or result_limit <= 0:
        raise ToolError("Choose a positive result_limit for this collection.")
    report = await run_report("theharvester", ["-d", domain, "-b", "crtsh", "-l", str(result_limit), "-f", "report"], "report.jsonl")
    try:
        records = [json.loads(line) for line in report.splitlines() if line.strip()]
        if not records or any(not isinstance(item, dict) for item in records):
            raise ValueError
        summary = records[0]
        if summary.get("type") != "summary" or summary.get("target") != domain:
            raise ValueError
        executions = summary.get("source_executions", [])
        if (summary.get("evidence_status") != "complete" or len(executions) != 1
                or executions[0].get("source") != "crtsh" or executions[0].get("status") != "completed"):
            raise ToolError("theHarvester's crtsh collection did not complete. No absence claim was accepted; inspect source availability before another scan.")
        findings = [item for item in records[1:] if item.get("type") == "hostname"
                    and isinstance(item.get("value"), str)
                    and (item["value"] == domain or item["value"].endswith("." + domain))]
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ToolError("theHarvester returned an unsupported JSONL report.") from None
    return {"domain": domain, "source_url": "https://crt.sh/?" + urlencode({"q": f"%.{domain}"}),
            "collection": summary, "hostnames": findings, "identity_verified": False,
            "limitation": "Certificate records are historical domain leads, not proof of employment, ownership or live services."}


def google_dork_links(subject: str, domain: str = "") -> dict:
    """Generate public search links locally; these are plans, never search evidence."""
    subject = subject.strip()
    if not subject or len(subject) > 1000 or any(c in subject for c in '\r\n"'):
        raise ValueError("Provide a nonblank subject without quotes or line breaks (at most 1,000 characters).")
    if domain and not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", domain):
        raise ValueError("Provide a domain without a URL or search operators.")
    queries = [f'"{subject}" site:github.com', f'"{subject}" site:linkedin.com/in',
               f'"{subject}" filetype:pdf']
    if domain:
        queries.append(f'"{subject}" site:{domain.lower()}')
    return {"executed": False, "queries": [{"query": q, "url": "https://www.google.com/search?" + urlencode({"q": q})} for q in queries]}
