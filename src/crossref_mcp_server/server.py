#!/usr/bin/env python3
"""
CrossRef MCP Server for Claude Desktop
=======================================
A comprehensive Model Context Protocol server for searching and retrieving
scholarly metadata from the CrossRef REST API.

Features:
- Keyword, title, author, and DOI-based search
- Journal lookup and journal-specific work retrieval
- Funder search
- Date and type filtering
- Reference list retrieval
- RIS and BibTeX export for Zotero integration

CrossRef API: https://api.crossref.org/
No API key required. Uses "polite" pool with mailto for better rate limits.

Usage with Claude Desktop - add to claude_desktop_config.json:
{
    "mcpServers": {
        "crossref": {
            "command": "python3",
            "args": ["/path/to/crossref_mcp_server.py"],
            "env": {
                "CROSSREF_MAILTO": "your.email@example.com"
            }
        }
    }
}
"""

import os
import json
import logging
from typing import Optional
from datetime import datetime

import requests
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CROSSREF_BASE = "https://api.crossref.org/v1"
MAILTO = os.environ.get("CROSSREF_MAILTO", "")
USER_AGENT = "CrossRefMCPServer/1.0 (https://github.com/crossref-mcp; mailto:{})".format(
    MAILTO or "not-provided"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("crossref-mcp")

# Store last search results for export
_last_results: list[dict] = []

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request(endpoint: str, params: Optional[dict] = None) -> dict:
    """Make a request to the CrossRef API."""
    url = f"{CROSSREF_BASE}/{endpoint}"
    headers = {"User-Agent": USER_AGENT}
    if params is None:
        params = {}
    if MAILTO:
        params["mailto"] = MAILTO

    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _format_authors(item: dict) -> str:
    """Format author list from a CrossRef work item."""
    authors = item.get("author", [])
    if not authors:
        return "Unknown"
    parts = []
    for a in authors[:10]:  # Cap at 10 for display
        given = a.get("given", "")
        family = a.get("family", "")
        if family:
            parts.append(f"{family}, {given}".strip(", "))
        elif a.get("name"):
            parts.append(a["name"])
    if len(authors) > 10:
        parts.append("et al.")
    return "; ".join(parts)


def _extract_year(item: dict) -> str:
    """Extract publication year from a CrossRef work item."""
    for date_field in ["published-print", "published-online", "published", "created"]:
        dp = item.get(date_field)
        if dp and "date-parts" in dp and dp["date-parts"]:
            parts = dp["date-parts"][0]
            if parts and parts[0]:
                return str(parts[0])
    return "n.d."


def _extract_date(item: dict) -> str:
    """Extract full date from a CrossRef work item."""
    for date_field in ["published-print", "published-online", "published", "created"]:
        dp = item.get(date_field)
        if dp and "date-parts" in dp and dp["date-parts"]:
            parts = dp["date-parts"][0]
            if parts:
                date_str = str(parts[0]) if parts[0] else ""
                if len(parts) > 1 and parts[1]:
                    date_str += f"-{parts[1]:02d}" if isinstance(parts[1], int) else f"-{parts[1]}"
                if len(parts) > 2 and parts[2]:
                    date_str += f"-{parts[2]:02d}" if isinstance(parts[2], int) else f"-{parts[2]}"
                return date_str
    return "n.d."


def _format_work(item: dict, index: int = 0) -> str:
    """Format a single CrossRef work item for display."""
    title = (item.get("title") or ["No title"])[0]
    authors = _format_authors(item)
    year = _extract_year(item)
    doi = item.get("DOI", "No DOI")
    container = (item.get("container-title") or [""])[0]
    work_type = item.get("type", "unknown")
    volume = item.get("volume", "")
    issue = item.get("issue", "")
    page = item.get("page", "")

    # Build volume/issue/page string
    vol_str = ""
    if volume:
        vol_str = f"Vol. {volume}"
        if issue:
            vol_str += f"({issue})"
    if page:
        vol_str += f", pp. {page}" if vol_str else f"pp. {page}"

    # Cited-by count
    cited = item.get("is-referenced-by-count", 0)

    lines = []
    if index > 0:
        lines.append(f"--- Result {index} ---")
    lines.append(f"Title: {title}")
    lines.append(f"Authors: {authors}")
    lines.append(f"Year: {year}")
    if container:
        lines.append(f"Journal/Source: {container}")
    if vol_str:
        lines.append(f"Volume/Pages: {vol_str}")
    lines.append(f"Type: {work_type}")
    lines.append(f"DOI: {doi}")
    lines.append(f"URL: https://doi.org/{doi}")
    if cited:
        lines.append(f"Cited by: {cited}")

    # Abstract (if available)
    abstract = item.get("abstract", "")
    if abstract:
        # Strip JATS XML tags
        import re
        clean = re.sub(r"<[^>]+>", "", abstract).strip()
        if len(clean) > 500:
            clean = clean[:500] + "..."
        lines.append(f"Abstract: {clean}")

    return "\n".join(lines)


def _build_filter_string(
    year_from: Optional[str] = None,
    year_to: Optional[str] = None,
    work_type: Optional[str] = None,
    has_abstract: Optional[bool] = None,
    has_orcid: Optional[bool] = None,
    has_references: Optional[bool] = None,
    issn: Optional[str] = None,
    funder: Optional[str] = None,
) -> str:
    """Build a CrossRef filter query string."""
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}")
    if year_to:
        filters.append(f"until-pub-date:{year_to}")
    if work_type:
        filters.append(f"type:{work_type}")
    if has_abstract:
        filters.append("has-abstract:true")
    if has_orcid:
        filters.append("has-orcid:true")
    if has_references:
        filters.append("has-references:true")
    if issn:
        filters.append(f"issn:{issn}")
    if funder:
        filters.append(f"funder:{funder}")
    return ",".join(filters)


def _work_to_ris(item: dict) -> str:
    """Convert a CrossRef work item to RIS format."""
    work_type = item.get("type", "journal-article")
    ris_type_map = {
        "journal-article": "JOUR",
        "book": "BOOK",
        "book-chapter": "CHAP",
        "proceedings-article": "CONF",
        "dissertation": "THES",
        "report": "RPRT",
        "dataset": "DATA",
        "monograph": "BOOK",
        "edited-book": "EDBOOK",
        "reference-book": "BOOK",
        "posted-content": "UNPB",
    }
    ris_type = ris_type_map.get(work_type, "GEN")

    lines = [f"TY  - {ris_type}"]

    title = (item.get("title") or [""])[0]
    if title:
        lines.append(f"TI  - {title}")

    for author in item.get("author", []):
        given = author.get("given", "")
        family = author.get("family", "")
        if family:
            lines.append(f"AU  - {family}, {given}".rstrip(", "))

    container = (item.get("container-title") or [""])[0]
    if container:
        if work_type == "book-chapter":
            lines.append(f"T2  - {container}")
        else:
            lines.append(f"JO  - {container}")
            # Also add abbreviated title if available
            short = (item.get("short-container-title") or [""])[0]
            if short and short != container:
                lines.append(f"JA  - {short}")

    year = _extract_year(item)
    if year != "n.d.":
        lines.append(f"PY  - {year}")

    date = _extract_date(item)
    if date != "n.d.":
        lines.append(f"DA  - {date}")

    volume = item.get("volume", "")
    if volume:
        lines.append(f"VL  - {volume}")

    issue = item.get("issue", "")
    if issue:
        lines.append(f"IS  - {issue}")

    page = item.get("page", "")
    if page:
        if "-" in page:
            sp, ep = page.split("-", 1)
            lines.append(f"SP  - {sp.strip()}")
            lines.append(f"EP  - {ep.strip()}")
        else:
            lines.append(f"SP  - {page}")

    doi = item.get("DOI", "")
    if doi:
        lines.append(f"DO  - {doi}")
        lines.append(f"UR  - https://doi.org/{doi}")

    # Abstract
    abstract = item.get("abstract", "")
    if abstract:
        import re
        clean = re.sub(r"<[^>]+>", "", abstract).strip()
        lines.append(f"AB  - {clean}")

    # ISSNs
    for issn_val in item.get("ISSN", []):
        lines.append(f"SN  - {issn_val}")

    # Publisher
    publisher = item.get("publisher", "")
    if publisher:
        lines.append(f"PB  - {publisher}")

    # Language
    lang = item.get("language", "")
    if lang:
        lines.append(f"LA  - {lang}")

    lines.append("ER  - ")
    return "\n".join(lines)


def _work_to_bibtex(item: dict) -> str:
    """Convert a CrossRef work item to BibTeX format."""
    work_type = item.get("type", "journal-article")
    bib_type_map = {
        "journal-article": "article",
        "book": "book",
        "book-chapter": "incollection",
        "proceedings-article": "inproceedings",
        "dissertation": "phdthesis",
        "report": "techreport",
        "dataset": "misc",
        "monograph": "book",
        "posted-content": "unpublished",
    }
    bib_type = bib_type_map.get(work_type, "misc")

    doi = item.get("DOI", "unknown")
    # Create a citation key
    authors = item.get("author", [])
    first_author = authors[0].get("family", "Unknown") if authors else "Unknown"
    year = _extract_year(item)
    # Clean the key
    key = f"{first_author}{year}".replace(" ", "").replace(",", "")

    title = (item.get("title") or [""])[0]
    container = (item.get("container-title") or [""])[0]
    volume = item.get("volume", "")
    issue = item.get("issue", "")
    page = item.get("page", "")
    publisher = item.get("publisher", "")

    # Format authors for BibTeX
    author_list = []
    for a in item.get("author", []):
        given = a.get("given", "")
        family = a.get("family", "")
        if family:
            author_list.append(f"{family}, {given}".rstrip(", "))
    author_str = " and ".join(author_list)

    lines = [f"@{bib_type}{{{key},"]
    if title:
        lines.append(f"  title = {{{title}}},")
    if author_str:
        lines.append(f"  author = {{{author_str}}},")
    if container:
        field = "booktitle" if bib_type in ("incollection", "inproceedings") else "journal"
        lines.append(f"  {field} = {{{container}}},")
    if year != "n.d.":
        lines.append(f"  year = {{{year}}},")
    if volume:
        lines.append(f"  volume = {{{volume}}},")
    if issue:
        lines.append(f"  number = {{{issue}}},")
    if page:
        lines.append(f"  pages = {{{page}}},")
    if publisher:
        lines.append(f"  publisher = {{{publisher}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
        lines.append(f"  url = {{https://doi.org/{doi}}},")
    lines.append("}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "CrossRef",
    instructions="Search and retrieve scholarly metadata from CrossRef's database of 150M+ records across all publishers and disciplines.",
)


@mcp.tool()
def crossref_search(
    query: str,
    count: int = 25,
    sort: str = "relevance",
    year_from: Optional[str] = None,
    year_to: Optional[str] = None,
    work_type: Optional[str] = None,
    has_abstract: Optional[bool] = None,
) -> str:
    """
    Search CrossRef for scholarly works by keyword.

    Args:
        query: Search terms (searches titles, abstracts, authors, full text)
        count: Number of results (max 50, default 25)
        sort: Sort order - "relevance" (default), "published" (newest first),
              "is-referenced-by-count" (most cited), "references-count"
        year_from: Filter to works published from this year (e.g. "2020")
        year_to: Filter to works published up to this year (e.g. "2025")
        work_type: Filter by type - "journal-article", "book-chapter", "book",
                   "proceedings-article", "dissertation", "report", "dataset",
                   "posted-content", "monograph"
        has_abstract: If True, only return works with abstracts
    """
    global _last_results

    params = {
        "query": query,
        "rows": min(count, 50),
    }

    if sort == "published":
        params["sort"] = "published"
        params["order"] = "desc"
    elif sort == "is-referenced-by-count":
        params["sort"] = "is-referenced-by-count"
        params["order"] = "desc"
    elif sort == "references-count":
        params["sort"] = "references-count"
        params["order"] = "desc"

    filter_str = _build_filter_string(
        year_from=year_from,
        year_to=year_to,
        work_type=work_type,
        has_abstract=has_abstract,
    )
    if filter_str:
        params["filter"] = filter_str

    try:
        data = _request("works", params)
        msg = data.get("message", {})
        total = msg.get("total-results", 0)
        items = msg.get("items", [])

        _last_results = items

        if not items:
            return f"No results found for '{query}'."

        output = [f"CrossRef Search: '{query}' — {total:,} total results (showing {len(items)})\n"]
        for i, item in enumerate(items, 1):
            output.append(_format_work(item, i))
            output.append("")

        return "\n".join(output)

    except requests.HTTPError as e:
        return f"CrossRef API error: {e}"
    except Exception as e:
        return f"Error searching CrossRef: {e}"


@mcp.tool()
def crossref_title_search(
    title: str,
    count: int = 10,
    year_from: Optional[str] = None,
    year_to: Optional[str] = None,
) -> str:
    """
    Search CrossRef specifically by title. More precise than keyword search
    when you know the title or partial title of a work.

    Args:
        title: Title or partial title to search for
        count: Number of results (max 50, default 10)
        year_from: Filter to works published from this year
        year_to: Filter to works published up to this year
    """
    global _last_results

    params = {
        "query.title": title,
        "rows": min(count, 50),
    }

    filter_str = _build_filter_string(year_from=year_from, year_to=year_to)
    if filter_str:
        params["filter"] = filter_str

    try:
        data = _request("works", params)
        msg = data.get("message", {})
        total = msg.get("total-results", 0)
        items = msg.get("items", [])

        _last_results = items

        if not items:
            return f"No results found for title '{title}'."

        output = [f"CrossRef Title Search: '{title}' — {total:,} total results (showing {len(items)})\n"]
        for i, item in enumerate(items, 1):
            output.append(_format_work(item, i))
            output.append("")

        return "\n".join(output)

    except Exception as e:
        return f"Error searching CrossRef: {e}"


@mcp.tool()
def crossref_author_search(
    author: str,
    query: Optional[str] = None,
    count: int = 25,
    year_from: Optional[str] = None,
    year_to: Optional[str] = None,
    sort: str = "relevance",
) -> str:
    """
    Search CrossRef for works by a specific author, optionally combined
    with a topic keyword.

    Args:
        author: Author name to search (e.g. "Smith" or "Jane Smith")
        query: Optional additional keywords to narrow results
        count: Number of results (max 50, default 25)
        year_from: Filter to works published from this year
        year_to: Filter to works published up to this year
        sort: "relevance" (default), "published", "is-referenced-by-count"
    """
    global _last_results

    params = {
        "query.author": author,
        "rows": min(count, 50),
    }

    if query:
        params["query"] = query

    if sort == "published":
        params["sort"] = "published"
        params["order"] = "desc"
    elif sort == "is-referenced-by-count":
        params["sort"] = "is-referenced-by-count"
        params["order"] = "desc"

    filter_str = _build_filter_string(year_from=year_from, year_to=year_to)
    if filter_str:
        params["filter"] = filter_str

    try:
        data = _request("works", params)
        msg = data.get("message", {})
        total = msg.get("total-results", 0)
        items = msg.get("items", [])

        _last_results = items

        if not items:
            return f"No results found for author '{author}'."

        label = f"CrossRef Author Search: '{author}'"
        if query:
            label += f" + '{query}'"
        output = [f"{label} — {total:,} total results (showing {len(items)})\n"]
        for i, item in enumerate(items, 1):
            output.append(_format_work(item, i))
            output.append("")

        return "\n".join(output)

    except Exception as e:
        return f"Error searching CrossRef: {e}"


@mcp.tool()
def crossref_doi_lookup(doi: str) -> str:
    """
    Retrieve full metadata for a specific work by its DOI.

    Args:
        doi: The DOI to look up (e.g. "10.1038/nature14539")
    """
    global _last_results

    # Clean DOI
    doi = doi.strip()
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    elif doi.startswith("http://doi.org/"):
        doi = doi[len("http://doi.org/"):]
    elif doi.startswith("doi:"):
        doi = doi[4:]

    try:
        data = _request(f"works/{doi}")
        item = data.get("message", {})

        _last_results = [item]

        lines = [_format_work(item)]

        # Additional details for single-item lookup
        publisher = item.get("publisher", "")
        if publisher:
            lines.append(f"Publisher: {publisher}")

        # ISSNs
        issns = item.get("ISSN", [])
        if issns:
            lines.append(f"ISSN: {', '.join(issns)}")

        # License
        licenses = item.get("license", [])
        if licenses:
            lic_urls = [lic.get("URL", "") for lic in licenses if lic.get("URL")]
            if lic_urls:
                lines.append(f"License: {', '.join(lic_urls)}")

        # Funders
        funders = item.get("funder", [])
        if funders:
            funder_strs = []
            for f in funders:
                name = f.get("name", "Unknown")
                awards = f.get("award", [])
                if awards:
                    funder_strs.append(f"{name} (awards: {', '.join(awards)})")
                else:
                    funder_strs.append(name)
            lines.append(f"Funders: {'; '.join(funder_strs)}")

        # Reference count
        ref_count = item.get("references-count", 0)
        if ref_count:
            lines.append(f"References: {ref_count}")

        # Subjects
        subjects = item.get("subject", [])
        if subjects:
            lines.append(f"Subjects: {', '.join(subjects)}")

        return "\n".join(lines)

    except requests.HTTPError as e:
        if e.response and e.response.status_code == 404:
            return f"DOI not found in CrossRef: {doi}"
        return f"CrossRef API error: {e}"
    except Exception as e:
        return f"Error looking up DOI: {e}"


@mcp.tool()
def crossref_journal_search(
    query: str,
    count: int = 20,
) -> str:
    """
    Search for journals in the CrossRef database.

    Args:
        query: Journal name or keywords (e.g. "educational psychology")
        count: Number of results (max 50, default 20)
    """
    params = {
        "query": query,
        "rows": min(count, 50),
    }

    try:
        data = _request("journals", params)
        msg = data.get("message", {})
        total = msg.get("total-results", 0)
        items = msg.get("items", [])

        if not items:
            return f"No journals found for '{query}'."

        output = [f"CrossRef Journal Search: '{query}' — {total:,} total results (showing {len(items)})\n"]
        for i, item in enumerate(items, 1):
            title = item.get("title", "Unknown")
            publisher = item.get("publisher", "Unknown")
            issns = item.get("ISSN", [])
            subjects = item.get("subjects", [])
            subject_str = ", ".join(s.get("name", "") for s in subjects) if subjects else ""
            counts = item.get("counts", {})
            total_dois = counts.get("total-dois", 0)

            lines = [f"--- Journal {i} ---"]
            lines.append(f"Title: {title}")
            lines.append(f"Publisher: {publisher}")
            if issns:
                lines.append(f"ISSN: {', '.join(issns)}")
            if subject_str:
                lines.append(f"Subjects: {subject_str}")
            if total_dois:
                lines.append(f"Total DOIs: {total_dois:,}")
            output.append("\n".join(lines))
            output.append("")

        return "\n".join(output)

    except Exception as e:
        return f"Error searching journals: {e}"


@mcp.tool()
def crossref_journal_works(
    issn: str,
    query: Optional[str] = None,
    count: int = 25,
    sort: str = "published",
    year_from: Optional[str] = None,
    year_to: Optional[str] = None,
) -> str:
    """
    Get works published in a specific journal by its ISSN.

    Args:
        issn: Journal ISSN (e.g. "0028-0836" for Nature)
        query: Optional keywords to filter within the journal
        count: Number of results (max 50, default 25)
        sort: "published" (newest first, default), "relevance", "is-referenced-by-count"
        year_from: Filter to works from this year
        year_to: Filter to works up to this year
    """
    global _last_results

    params = {"rows": min(count, 50)}

    if query:
        params["query"] = query

    if sort == "published":
        params["sort"] = "published"
        params["order"] = "desc"
    elif sort == "is-referenced-by-count":
        params["sort"] = "is-referenced-by-count"
        params["order"] = "desc"

    filter_str = _build_filter_string(year_from=year_from, year_to=year_to)
    if filter_str:
        params["filter"] = filter_str

    try:
        data = _request(f"journals/{issn}/works", params)
        msg = data.get("message", {})
        total = msg.get("total-results", 0)
        items = msg.get("items", [])

        _last_results = items

        if not items:
            return f"No works found for ISSN {issn}."

        output = [f"CrossRef Journal Works (ISSN: {issn}) — {total:,} total (showing {len(items)})\n"]
        for i, item in enumerate(items, 1):
            output.append(_format_work(item, i))
            output.append("")

        return "\n".join(output)

    except Exception as e:
        return f"Error retrieving journal works: {e}"


@mcp.tool()
def crossref_funder_search(
    query: str,
    count: int = 10,
) -> str:
    """
    Search for funding organizations in CrossRef.

    Args:
        query: Funder name or keywords (e.g. "National Science Foundation")
        count: Number of results (max 50, default 10)
    """
    params = {
        "query": query,
        "rows": min(count, 50),
    }

    try:
        data = _request("funders", params)
        msg = data.get("message", {})
        total = msg.get("total-results", 0)
        items = msg.get("items", [])

        if not items:
            return f"No funders found for '{query}'."

        output = [f"CrossRef Funder Search: '{query}' — {total:,} total (showing {len(items)})\n"]
        for i, item in enumerate(items, 1):
            name = item.get("name", "Unknown")
            funder_id = item.get("id", "")
            location = item.get("location", "")
            alt_names = item.get("alt-names", [])
            work_count = item.get("work-count", 0)

            lines = [f"--- Funder {i} ---"]
            lines.append(f"Name: {name}")
            if funder_id:
                lines.append(f"Funder DOI: 10.13039/{funder_id}")
            if location:
                lines.append(f"Location: {location}")
            if alt_names:
                lines.append(f"Also known as: {', '.join(alt_names[:5])}")
            if work_count:
                lines.append(f"Funded works: {work_count:,}")
            output.append("\n".join(lines))
            output.append("")

        return "\n".join(output)

    except Exception as e:
        return f"Error searching funders: {e}"


@mcp.tool()
def crossref_references(doi: str) -> str:
    """
    Get the reference list for a specific work by DOI.
    Returns the references cited by the given work.

    Args:
        doi: DOI of the work whose references you want
    """
    doi = doi.strip()
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]

    try:
        data = _request(f"works/{doi}")
        item = data.get("message", {})
        references = item.get("reference", [])

        if not references:
            return f"No references found for DOI {doi} (publisher may not have deposited them)."

        title = (item.get("title") or ["Unknown"])[0]
        output = [f"References from: {title}\nDOI: {doi}\nTotal references: {len(references)}\n"]

        for i, ref in enumerate(references, 1):
            ref_doi = ref.get("DOI", "")
            ref_title = ref.get("article-title", "") or ref.get("volume-title", "")
            ref_author = ref.get("author", "")
            ref_year = ref.get("year", "")
            ref_journal = ref.get("journal-title", "")
            unstructured = ref.get("unstructured", "")

            if unstructured and not ref_title:
                output.append(f"{i}. {unstructured}")
                if ref_doi:
                    output.append(f"   DOI: {ref_doi}")
            else:
                parts = []
                if ref_author:
                    parts.append(ref_author)
                if ref_year:
                    parts.append(f"({ref_year})")
                if ref_title:
                    parts.append(ref_title)
                if ref_journal:
                    parts.append(ref_journal)
                output.append(f"{i}. {' '.join(parts)}")
                if ref_doi:
                    output.append(f"   DOI: {ref_doi}")

            output.append("")

        return "\n".join(output)

    except requests.HTTPError as e:
        if e.response and e.response.status_code == 404:
            return f"DOI not found: {doi}"
        return f"CrossRef API error: {e}"
    except Exception as e:
        return f"Error retrieving references: {e}"


@mcp.tool()
def crossref_export_ris() -> str:
    """
    Export the most recent search results as RIS format.
    Import into Zotero: File → Import → paste or save as .ris file.
    """
    if not _last_results:
        return "No recent search results to export. Run a search first."

    ris_entries = []
    for item in _last_results:
        ris_entries.append(_work_to_ris(item))

    count = len(ris_entries)
    output = f"RIS Export ({count} records) — Copy and save as .ris file for Zotero import:\n\n"
    output += "\n\n".join(ris_entries)

    return output


@mcp.tool()
def crossref_export_bibtex() -> str:
    """
    Export the most recent search results as BibTeX format.
    """
    if not _last_results:
        return "No recent search results to export. Run a search first."

    bib_entries = []
    for item in _last_results:
        bib_entries.append(_work_to_bibtex(item))

    count = len(bib_entries)
    output = f"BibTeX Export ({count} records):\n\n"
    output += "\n\n".join(bib_entries)

    return output


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting CrossRef MCP Server...")
    if MAILTO:
        logger.info(f"Using polite pool with mailto: {MAILTO}")
    else:
        logger.info("No CROSSREF_MAILTO set — using public pool (lower rate limits)")
        logger.info("Set CROSSREF_MAILTO environment variable for better performance")
    mcp.run(transport="stdio")
