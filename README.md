# CrossRef MCP Server

An MCP (Model Context Protocol) server for searching and retrieving scholarly metadata from the [CrossRef](https://www.crossref.org/) REST API — 150M+ records across all publishers and disciplines.

Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk). No API key required.

## Tools

| Tool | Description |
|------|-------------|
| `crossref_search` | Search works by keyword with filtering by year, type, and sort options |
| `crossref_title_search` | Search specifically by title for more precise matching |
| `crossref_author_search` | Search for works by a specific author, optionally combined with keywords |
| `crossref_doi_lookup` | Retrieve full metadata for a work by DOI |
| `crossref_journal_search` | Search for journals by name |
| `crossref_journal_works` | Get works published in a specific journal by ISSN |
| `crossref_funder_search` | Search for funding organizations |
| `crossref_references` | Get the reference list cited by a specific work |
| `crossref_export_ris` | Export recent results as RIS (for Zotero, EndNote, etc.) |
| `crossref_export_bibtex` | Export recent results as BibTeX |

## Setup

### 1. Install

```bash
cd crossref-mcp-server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment (optional)

CrossRef doesn't require an API key, but setting a mailto address gives you access to their faster "polite" API pool:

```bash
cp .env.example .env
# Edit .env with your email address
```

### 3. Add to Claude Desktop

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "crossref": {
      "command": "/path/to/crossref-mcp-server/venv/bin/python",
      "args": ["/path/to/crossref-mcp-server/server.py"],
      "env": {
        "CROSSREF_MAILTO": "your.email@example.com"
      }
    }
  }
}
```

Or if using Claude Code CLI:

```bash
claude mcp add crossref \
  /path/to/crossref-mcp-server/venv/bin/python \
  /path/to/crossref-mcp-server/server.py \
  -e CROSSREF_MAILTO=your.email@example.com
```

## Usage examples

Once connected, you can ask Claude things like:

- "Search CrossRef for recent papers on transformer architectures"
- "Find works by Jane Smith on educational psychology from 2020-2024"
- "Look up the metadata for DOI 10.1038/nature14539"
- "Search for journals about machine learning"
- "Get the reference list for this paper and export as RIS for Zotero"

## License

MIT
