"""Script to export OpenAPI specification to markdown documentation."""

from pathlib import Path

from app.main import app

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)
DOCS_FILE = DOCS_DIR / "API.md"


def generate_markdown_docs() -> None:
    """Generate docs/API.md from the FastAPI application OpenAPI schema."""
    openapi = app.openapi()

    lines: list[str] = [
        f"# {openapi.get('info', {}).get('title', 'ColdReach API Reference')}",
        "",
        f"**Version**: {openapi.get('info', {}).get('version', '1.0.0')}  ",
        "**Base URL**: `http://127.0.0.1:8000/api/v1`  ",
        "**Allowed CORS Origin**: `http://localhost:5173` only  ",
        "",
        "## Security & Authentication",
        "",
        "Every endpoint under `/api/v1` requires authentication via the HTTP header:",
        "```http",
        "X-API-Token: <your_secret_token>",
        "```",
        "Generate a random token in your `.env` file via:",
        "```bash",
        'python -c "import secrets; print(secrets.token_urlsafe(32))"',
        "```",
        "",
        "---",
        "",
        "## API Endpoints Summary",
        "",
        "| Method | Path | Summary | Tags |",
        "|:---|:---|:---|:---|",
    ]

    paths = openapi.get("paths", {})
    sorted_paths = sorted(paths.items())

    for path, methods in sorted_paths:
        for method, details in methods.items():
            if method in ("get", "post", "put", "patch", "delete"):
                summary = details.get("summary") or details.get("description", "")
                summary = summary.split("\n")[0].replace("|", "\\|")
                tags = ", ".join(details.get("tags", []))
                lines.append(f"| `{method.upper()}` | `{path}` | {summary} | {tags} |")

    lines.extend(
        [
            "",
            "---",
            "",
            "## Detailed Endpoint Specifications",
            "",
        ]
    )

    for path, methods in sorted_paths:
        for method, details in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue

            summary = details.get("summary", "")
            description = details.get("description", "")
            tags = ", ".join(details.get("tags", []))

            lines.append(f"### `{method.upper()}` {path}")
            if tags:
                lines.append(f"**Category**: {tags}  ")
            if summary:
                lines.append(f"**Summary**: {summary}  ")
            if description and description != summary:
                lines.append(f"\n{description}\n")

            # Parameters
            params = details.get("parameters", [])
            if params:
                lines.append("\n**Parameters**:")
                lines.append("| Name | In | Type | Required | Description |")
                lines.append("|:---|:---|:---|:---|:---|")
                for p in params:
                    p_name = p.get("name")
                    p_in = p.get("in")
                    p_req = "Yes" if p.get("required") else "No"
                    p_type = p.get("schema", {}).get("type", "string")
                    p_desc = p.get("description", "").replace("|", "\\|")
                    lines.append(f"| `{p_name}` | {p_in} | `{p_type}` | {p_req} | {p_desc} |")

            # Responses
            responses = details.get("responses", {})
            if responses:
                lines.append("\n**Responses**:")
                for status_code, resp_desc in responses.items():
                    desc = resp_desc.get("description", "")
                    lines.append(f"- **`{status_code}`**: {desc}")

            lines.append("\n---\n")

    DOCS_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Generated API reference at: {DOCS_FILE}")


if __name__ == "__main__":
    generate_markdown_docs()
