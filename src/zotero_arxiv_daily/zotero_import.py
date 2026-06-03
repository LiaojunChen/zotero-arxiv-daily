from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from loguru import logger

from .protocol import Paper


DEFAULT_COLLECTION_NAME = "每日论文推送"
ISSUE_TITLE_PREFIX = "[Add to Zotero]"
ISSUE_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class ImportPayload:
    url: str
    source: str = "arxiv"
    title: str | None = None
    pdf_url: str | None = None
    collection_name: str = DEFAULT_COLLECTION_NAME
    upload_pdf: bool = True


@dataclass
class PaperMetadata:
    source: str
    title: str
    authors: list[str]
    abstract: str
    url: str
    pdf_url: str | None = None
    arxiv_id: str | None = None
    published: str | None = None


def build_add_to_zotero_issue_url(
    paper: Paper,
    github_repo: str,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    upload_pdf: bool = True,
) -> str:
    repo = normalize_github_repo(github_repo)
    payload = {
        "source": paper.source,
        "title": paper.title,
        "url": paper.url,
        "pdf_url": paper.pdf_url,
        "collection_name": collection_name,
        "upload_pdf": upload_pdf,
    }
    body = (
        "Submit this issue to add the paper to Zotero.\n\n"
        "```json\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )
    title = f"{ISSUE_TITLE_PREFIX} {paper.title}"[:250]
    return f"https://github.com/{repo}/issues/new?{urlencode({'title': title, 'body': body})}"


def normalize_github_repo(github_repo: str) -> str:
    repo = github_repo.strip()
    repo = repo.removeprefix("https://github.com/")
    repo = repo.removeprefix("http://github.com/")
    repo = repo.removeprefix("github.com/")
    return repo.strip("/")


def parse_issue_body(body: str) -> ImportPayload:
    match = ISSUE_JSON_RE.search(body)
    raw_payload = match.group(1) if match else body.strip()
    try:
        data = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Issue body must contain a JSON payload.") from exc
    return import_payload_from_dict(data)


def import_payload_from_dict(data: dict) -> ImportPayload:
    url = str(data.get("url") or "").strip()
    if not url:
        raise ValueError("Import payload must include a paper url.")
    return ImportPayload(
        source=str(data.get("source") or "arxiv"),
        title=data.get("title"),
        url=url,
        pdf_url=data.get("pdf_url"),
        collection_name=str(data.get("collection_name") or DEFAULT_COLLECTION_NAME),
        upload_pdf=parse_bool(data.get("upload_pdf"), default=True),
    )


def parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def extract_arxiv_id(url: str) -> str | None:
    match = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^?#/]+)", url)
    if match:
        raw_id = match.group(1)
    elif re.fullmatch(r"(?:arxiv:)?[a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5}(?:v\d+)?", url, re.I):
        raw_id = url
    else:
        return None

    raw_id = raw_id.strip().removeprefix("arXiv:").removeprefix("arxiv:")
    raw_id = raw_id.removesuffix(".pdf")
    return re.sub(r"v\d+$", "", raw_id)


def fetch_paper_metadata(payload: ImportPayload) -> PaperMetadata:
    if payload.source == "arxiv" or "arxiv.org" in payload.url:
        return fetch_arxiv_metadata(payload)

    if not payload.title:
        raise ValueError("Non-arXiv imports must include a title in the payload.")

    return PaperMetadata(
        source=payload.source,
        title=payload.title,
        authors=[],
        abstract="",
        url=payload.url,
        pdf_url=payload.pdf_url,
    )


def fetch_arxiv_metadata(payload: ImportPayload) -> PaperMetadata:
    arxiv_id = extract_arxiv_id(payload.url)
    if not arxiv_id:
        raise ValueError(f"Could not extract arXiv id from {payload.url}")

    import arxiv

    client = arxiv.Client()
    search = arxiv.Search(id_list=[arxiv_id])
    result = next(client.results(search), None)
    if result is None:
        raise ValueError(f"Could not fetch arXiv metadata for {arxiv_id}")

    published = result.published.date().isoformat() if result.published else None
    return PaperMetadata(
        source="arxiv",
        title=result.title or payload.title or arxiv_id,
        authors=[str(author) for author in result.authors],
        abstract=result.summary or "",
        url=result.entry_id or payload.url,
        pdf_url=payload.pdf_url or result.pdf_url,
        arxiv_id=arxiv_id,
        published=published,
    )


def ensure_collection(zot, collection_name: str) -> str:
    collections = zot.everything(zot.collections())
    for collection in collections:
        if collection.get("data", {}).get("name") == collection_name:
            return collection["key"]

    response = zot.create_collections([{"name": collection_name}])
    success = response.get("success") or response.get("successful") or {}
    if success:
        return next(iter(success.values()))

    raise RuntimeError(f"Failed to create Zotero collection {collection_name}: {response}")


def find_existing_item(zot, paper: PaperMetadata):
    queries = [paper.arxiv_id, paper.title, paper.url]
    seen = set()
    for query in [q for q in queries if q]:
        for item in zot.everything(zot.items(q=query)):
            key = item.get("key")
            if key in seen:
                continue
            seen.add(key)
            if is_same_paper(item, paper):
                return item
    return None


def is_same_paper(item: dict, paper: PaperMetadata) -> bool:
    data = item.get("data", {})
    haystack = "\n".join(
        str(value or "")
        for value in [
            data.get("title"),
            data.get("url"),
            data.get("archiveID"),
            data.get("DOI"),
            data.get("extra"),
        ]
    )
    if paper.arxiv_id and paper.arxiv_id in haystack:
        return True
    if paper.url and paper.url == data.get("url"):
        return True
    return normalize_title(paper.title) == normalize_title(data.get("title", ""))


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip().lower()


def ensure_item_in_collection(zot, item: dict, collection_key: str) -> None:
    collections = item.get("data", {}).get("collections", [])
    if collection_key in collections:
        return
    response = zot.addto_collection(collection_key, item)
    response.raise_for_status()


def create_zotero_item(zot, paper: PaperMetadata, collection_key: str, collection_name: str) -> str:
    try:
        item = zot.item_template("preprint")
    except Exception as exc:
        logger.warning(f"Could not load Zotero preprint template, using a minimal template: {exc}")
        item = {
            "itemType": "preprint",
            "title": "",
            "creators": [],
            "abstractNote": "",
            "url": "",
            "date": "",
            "archive": "",
            "archiveID": "",
            "extra": "",
            "collections": [],
            "tags": [],
        }
    item["title"] = paper.title
    item["creators"] = [author_to_creator(author) for author in paper.authors]
    item["collections"] = [collection_key]
    item["tags"] = [{"tag": collection_name}]

    set_if_present(item, "abstractNote", paper.abstract)
    set_if_present(item, "url", paper.url)
    set_if_present(item, "date", paper.published)
    set_if_present(item, "archive", "arXiv" if paper.arxiv_id else None)
    set_if_present(item, "archiveID", paper.arxiv_id)

    extra_lines = []
    if paper.arxiv_id:
        extra_lines.append(f"arXiv: {paper.arxiv_id}")
    if paper.pdf_url:
        extra_lines.append(f"PDF: {paper.pdf_url}")
    if extra_lines:
        set_if_present(item, "extra", "\n".join(extra_lines))

    response = zot.create_items([item])
    success = response.get("success") or response.get("successful") or {}
    if success:
        return next(iter(success.values()))

    raise RuntimeError(f"Failed to create Zotero item: {response}")


def set_if_present(item: dict, key: str, value) -> None:
    if value is not None and key in item:
        item[key] = value


def author_to_creator(author: str) -> dict:
    author = author.strip()
    if not author:
        return {"creatorType": "author", "name": "Unknown"}
    if "," in author:
        last_name, first_name = [part.strip() for part in author.split(",", 1)]
        return {"creatorType": "author", "firstName": first_name, "lastName": last_name}
    parts = author.split()
    if len(parts) == 1:
        return {"creatorType": "author", "name": author}
    return {"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]}


def item_has_pdf_attachment(zot, item_key: str) -> bool:
    try:
        children = zot.everything(zot.children(item_key))
    except Exception as exc:
        logger.warning(f"Could not inspect Zotero child attachments for {item_key}: {exc}")
        return False

    for child in children:
        data = child.get("data", {})
        if data.get("itemType") != "attachment":
            continue
        content_type = data.get("contentType") or ""
        title = data.get("title") or ""
        url = data.get("url") or ""
        if "pdf" in content_type.lower() or title.lower().endswith(".pdf") or url.lower().endswith(".pdf"):
            return True
    return False


def upload_pdf_attachment(zot, paper: PaperMetadata, item_key: str) -> None:
    if not paper.pdf_url:
        raise ValueError("Paper has no PDF URL.")

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / safe_pdf_filename(paper)
        with urlopen(paper.pdf_url, timeout=90) as response, path.open("wb") as output:
            shutil.copyfileobj(response, output)
        zot.attachment_both([(path.name, str(path))], parentid=item_key)


def safe_pdf_filename(paper: PaperMetadata) -> str:
    stem = paper.arxiv_id or paper.title
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_")[:120]
    return f"{stem or 'paper'}.pdf"


def import_paper_to_zotero(zot, payload: ImportPayload) -> dict:
    paper = fetch_paper_metadata(payload)
    collection_key = ensure_collection(zot, payload.collection_name)
    existing_item = find_existing_item(zot, paper)

    if existing_item:
        item_key = existing_item["key"]
        ensure_item_in_collection(zot, existing_item, collection_key)
        status = "existing"
    else:
        item_key = create_zotero_item(zot, paper, collection_key, payload.collection_name)
        status = "created"

    pdf_status = "skipped"
    if payload.upload_pdf and paper.pdf_url:
        if item_has_pdf_attachment(zot, item_key):
            pdf_status = "existing"
        else:
            upload_pdf_attachment(zot, paper, item_key)
            pdf_status = "uploaded"

    return {
        "status": status,
        "item_key": item_key,
        "collection_key": collection_key,
        "collection_name": payload.collection_name,
        "pdf_status": pdf_status,
        "title": paper.title,
        "url": paper.url,
    }


def write_github_output(values: dict) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as output:
        for key, value in values.items():
            safe_value = str(value).replace("\n", " ")
            output.write(f"{key}={safe_value}\n")
