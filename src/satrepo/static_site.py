"""Render Standard.site records into static HTML files."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .config import RepoConfig
from .jsonio import read_json, write_json_atomic
from .keys import read_private_key
from .paths import RepoPaths
from .standard_site import DOCUMENT_COLLECTION, MARKDOWN_TYPE, PUBLICATION_COLLECTION
from .storage_static import StaticStorage

PUBLIC_DIR_MODE = 0o755
PUBLIC_FILE_MODE = 0o644
GENERATED_REGISTRY = "standard_site_generated.json"


@dataclass(frozen=True)
class RepoRecord:
    collection: str
    rkey: str
    record: dict[str, Any]


@dataclass(frozen=True)
class Publication:
    rkey: str
    uri: str
    record: dict[str, Any]
    url: str
    site_prefix: str


@dataclass(frozen=True)
class Document:
    rkey: str
    uri: str
    record: dict[str, Any]
    page_path: str
    canonical_url: str
    publication: Publication


def render_standard_site(paths: RepoPaths, config: RepoConfig) -> None:
    """Render committed Standard.site records into site/."""

    records = _committed_records(paths, config)
    publications = _publications(config, records)
    documents = _documents(config, records, publications)

    previous_paths = _read_generated_registry(paths)
    generated_paths: set[str] = set()
    _remove_previous_generated_files(paths, previous_paths)

    for publication in publications.values():
        generated_paths.update(_write_publication_verification(paths, publication))
        generated_paths.add(_write_publication_index(paths, publication, documents))

    for document in documents:
        generated_paths.add(_write_document(paths, document))

    write_json_atomic(paths.state / GENERATED_REGISTRY, sorted(generated_paths))


def _committed_records(paths: RepoPaths, config: RepoConfig) -> list[RepoRecord]:
    signing_key = read_private_key(config.key_dir / "signing.key")
    rotation_key = read_private_key(config.key_dir / "rotation.key")
    storage = StaticStorage(
        paths=paths,
        config=config,
        signing_key=signing_key,
        rotation_key=rotation_key,
    )
    repo = storage.load_repo(config.did)
    if repo is None:
        return []

    return [
        RepoRecord(collection=collection, rkey=rkey, record=record)
        for collection, records in sorted(repo.get_contents().items())
        for rkey, record in sorted(records.items())
    ]


def _publications(config: RepoConfig, records: list[RepoRecord]) -> dict[str, Publication]:
    publications: dict[str, Publication] = {}
    for item in records:
        if item.collection != PUBLICATION_COLLECTION:
            continue
        url = item.record.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        normalized_url = url.rstrip("/") or url
        uri = f"at://{config.did}/{PUBLICATION_COLLECTION}/{item.rkey}"
        publications[uri] = Publication(
            rkey=item.rkey,
            uri=uri,
            record=item.record,
            url=normalized_url,
            site_prefix=_url_path_prefix(normalized_url),
        )
    return publications


def _documents(
    config: RepoConfig,
    records: list[RepoRecord],
    publications: dict[str, Publication],
) -> list[Document]:
    documents: list[Document] = []
    for item in records:
        if item.collection != DOCUMENT_COLLECTION:
            continue
        site = item.record.get("site")
        publication = publications.get(site) if isinstance(site, str) else None
        document_path = item.record.get("path")
        if (
            publication is None
            or not isinstance(document_path, str)
            or not document_path.startswith("/")
        ):
            continue

        uri = f"at://{config.did}/{DOCUMENT_COLLECTION}/{item.rkey}"
        page_path = _join_site_paths(publication.site_prefix, document_path)
        documents.append(
            Document(
                rkey=item.rkey,
                uri=uri,
                record=item.record,
                page_path=page_path,
                canonical_url=_join_url(publication.url, document_path),
                publication=publication,
            )
        )

    return sorted(
        documents,
        key=lambda doc: (doc.record.get("publishedAt", ""), doc.rkey),
        reverse=True,
    )


def _write_publication_verification(paths: RepoPaths, publication: Publication) -> set[str]:
    if publication.site_prefix in ("", "/"):
        rel_path = ".well-known/site.standard.publication"
    else:
        rel_path = _join_site_paths(
            "/.well-known/site.standard.publication",
            publication.site_prefix,
        )

    _write_text(paths.site / rel_path.lstrip("/"), publication.uri)
    return {rel_path.lstrip("/")}


def _write_publication_index(
    paths: RepoPaths,
    publication: Publication,
    documents: list[Document],
) -> str:
    publication_documents = [
        document for document in documents if document.publication.uri == publication.uri
    ]
    items = "\n".join(_publication_document_item(document) for document in publication_documents)
    if not items:
        items = '<p class="muted">No documents yet.</p>'

    title = _string(publication.record.get("name")) or publication.url
    description = _string(publication.record.get("description"))
    description_html = f"<p>{html.escape(description)}</p>" if description else ""
    body = f"""\
<header>
  <h1>{html.escape(title)}</h1>
  {description_html}
</header>
<main>
  {items}
</main>
"""
    path = _html_file_path(publication.site_prefix or "/")
    _write_html(
        paths.site / path,
        title=title,
        body=body,
        canonical_url=publication.url,
        extra_head=(
            f'<link rel="alternate" type="application/atom+xml" '
            f'href="{html.escape(_join_url(publication.url, "/atom.xml"))}">'
        ),
    )
    return path


def _publication_document_item(document: Document) -> str:
    title = _string(document.record.get("title")) or document.uri
    published = _string(document.record.get("publishedAt"))
    description = _string(document.record.get("description"))
    time_html = (
        f'<time datetime="{html.escape(published)}">{html.escape(published)}</time>'
        if published
        else ""
    )
    description_html = f"<p>{html.escape(description)}</p>" if description else ""
    href = _site_href(document.page_path)
    return f"""\
<article class="listing">
  {time_html}
  <h2><a href="{html.escape(href)}">{html.escape(title)}</a></h2>
  {description_html}
</article>"""


def _write_document(paths: RepoPaths, document: Document) -> str:
    title = _string(document.record.get("title")) or document.uri
    published = _string(document.record.get("publishedAt"))
    description = _string(document.record.get("description"))
    tags = _string_list(document.record.get("tags"))

    time_html = (
        f'<time datetime="{html.escape(published)}">{html.escape(published)}</time>'
        if published
        else ""
    )
    description_meta = (
        f'<meta name="description" content="{html.escape(description, quote=True)}">'
        if description
        else ""
    )
    tag_html = ""
    if tags:
        tag_items = "".join(f"<li>{html.escape(tag)}</li>" for tag in tags)
        tag_html = f'<ul class="tags">{tag_items}</ul>'

    publication_name = _string(document.publication.record.get("name")) or document.publication.url
    publication_href = _site_href(document.publication.site_prefix or "/")
    body = f"""\
<article>
  <header>
    <p><a href="{html.escape(publication_href)}">{html.escape(publication_name)}</a></p>
    <h1>{html.escape(title)}</h1>
    {time_html}
    {tag_html}
  </header>
  {_render_document_body(document.record, title=title)}
</article>
"""
    path = _html_file_path(document.page_path)
    _write_html(
        paths.site / path,
        title=title,
        body=body,
        canonical_url=document.canonical_url,
        extra_head="\n".join(
            value
            for value in (
                description_meta,
                f'<link rel="alternate" href="{html.escape(document.uri, quote=True)}">',
                (
                    f'<link rel="site.standard.document" '
                    f'href="{html.escape(document.uri, quote=True)}">'
                ),
            )
            if value
        ),
    )
    return path


def _render_document_body(record: dict[str, Any], *, title: str) -> str:
    content = record.get("content")
    if isinstance(content, dict) and content.get("$type") == MARKDOWN_TYPE:
        text = content.get("text")
        if isinstance(text, str):
            return _render_markdown(_drop_duplicate_title_heading(text, title))

    text_content = record.get("textContent")
    if isinstance(text_content, str):
        return _render_plain_text(text_content)

    return ""


def _drop_duplicate_title_heading(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        match = re.match(r"^#\s+(.+)$", line.strip())
        if not match or match.group(1).strip() != title.strip():
            return markdown

        remaining = lines[index + 1 :]
        if remaining and not remaining[0].strip():
            remaining = remaining[1:]
        return "\n".join(remaining)
    return markdown


def _render_markdown(markdown: str) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(f"<p>{_inline_markdown(' '.join(paragraph))}</p>")
            paragraph = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            items = "".join(f"<li>{_inline_markdown(item)}</li>" for item in list_items)
            blocks.append(f"<ul>{items}</ul>")
            list_items = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if code_lines is not None:
            if line.startswith("```"):
                code = html.escape("\n".join(code_lines))
                blocks.append(f"<pre><code>{code}</code></pre>")
                code_lines = None
            else:
                code_lines.append(raw_line)
            continue

        if line.startswith("```"):
            flush_paragraph()
            flush_list()
            code_lines = []
            continue
        if not line:
            flush_paragraph()
            flush_list()
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            continue

        list_item = re.match(r"^[-*]\s+(.+)$", line)
        if list_item:
            flush_paragraph()
            list_items.append(list_item.group(1))
            continue

        flush_list()
        paragraph.append(line)

    flush_paragraph()
    flush_list()
    if code_lines is not None:
        code = html.escape("\n".join(code_lines))
        blocks.append(f"<pre><code>{code}</code></pre>")
    return "\n".join(blocks)


def _inline_markdown(value: str) -> str:
    escaped = html.escape(value)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def _render_plain_text(value: str) -> str:
    return "\n".join(
        f"<p>{html.escape(paragraph.strip())}</p>"
        for paragraph in value.split("\n\n")
        if paragraph.strip()
    )


def _write_html(
    path: Path,
    *,
    title: str,
    body: str,
    canonical_url: str,
    extra_head: str = "",
) -> None:
    escaped_title = html.escape(title)
    escaped_canonical = html.escape(canonical_url, quote=True)
    head_extra = f"\n  {extra_head}" if extra_head else ""
    content = f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <link rel="canonical" href="{escaped_canonical}">{head_extra}
  <style>
    body {{ color: #1f2933; font-family: system-ui, sans-serif; line-height: 1.6; margin: 0; }}
    main, article {{ max-width: 42rem; margin: 4rem auto; padding: 0 1rem; }}
    header {{ margin-bottom: 2rem; }}
    h1, h2 {{ line-height: 1.2; }}
    a {{ color: #0b63ce; }}
    pre {{ overflow-x: auto; padding: 1rem; background: #f3f4f6; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .listing {{ margin: 0 0 2rem; }}
    .muted, time {{ color: #64748b; }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 0.5rem; list-style: none; padding: 0; }}
    .tags li {{ border: 1px solid #cbd5e1; border-radius: 999px; padding: 0.125rem 0.5rem; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""
    _write_text(path, content.rstrip())


def _read_generated_registry(paths: RepoPaths) -> set[str]:
    path = paths.state / GENERATED_REGISTRY
    if not path.exists():
        return set()
    data = read_json(path)
    if not isinstance(data, list):
        return set()
    return {item for item in data if isinstance(item, str)}


def _remove_previous_generated_files(paths: RepoPaths, rel_paths: set[str]) -> None:
    for rel_path in sorted(rel_paths):
        if not _safe_relative_path(rel_path):
            continue
        path = paths.site / rel_path
        if path.exists() and path.is_file():
            path.unlink()
            _remove_empty_parents(path.parent, paths.site)


def _remove_empty_parents(path: Path, stop: Path) -> None:
    stop = stop.resolve()
    current = path.resolve()
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _write_text(path: Path, value: str) -> None:
    _ensure_public_dir(path.parent)
    path.write_text(f"{value}\n", encoding="utf-8")
    _make_public_file(path)


def _ensure_public_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod((path.stat().st_mode & 0o777) | PUBLIC_DIR_MODE)


def _make_public_file(path: Path) -> None:
    path.chmod((path.stat().st_mode & 0o777) | PUBLIC_FILE_MODE)


def _html_file_path(url_path: str) -> str:
    normalized = _normalize_site_path(url_path)
    if normalized == "/":
        return "index.html"
    if PurePosixPath(normalized).suffix:
        return normalized.lstrip("/")
    return f"{normalized.lstrip('/')}/index.html"


def _site_href(url_path: str) -> str:
    normalized = _normalize_site_path(url_path)
    if normalized == "/":
        return "/"
    return normalized


def _join_site_paths(prefix: str, suffix: str) -> str:
    prefix = "" if prefix == "/" else prefix.strip("/")
    suffix = suffix.strip("/")
    if prefix and suffix:
        return f"/{prefix}/{suffix}"
    if prefix:
        return f"/{prefix}"
    if suffix:
        return f"/{suffix}"
    return "/"


def _join_url(base_url: str, path: str) -> str:
    split = urlsplit(base_url)
    joined_path = _join_site_paths(split.path, path)
    return urlunsplit((split.scheme, split.netloc, joined_path, "", ""))


def _url_path_prefix(url: str) -> str:
    return _normalize_site_path(urlsplit(url).path)


def _normalize_site_path(value: str) -> str:
    path = "/" + value.strip("/")
    if path == "/.":
        return "/"
    return path


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
