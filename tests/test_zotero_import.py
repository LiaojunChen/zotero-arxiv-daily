from urllib.parse import parse_qs, urlsplit

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily import zotero_import
from zotero_arxiv_daily.zotero_import import (
    ImportPayload,
    PaperMetadata,
    build_add_to_zotero_issue_url,
    extract_arxiv_id,
    import_paper_to_zotero,
    import_payload_from_dict,
    override_attachment_options,
    parse_issue_body,
)


class StubZotero:
    def __init__(self):
        self.collections_data = []
        self.items_data = []
        self.created_items = []
        self.added_to_collection = []
        self.attachments = []

    def everything(self, value):
        return value

    def collections(self):
        return self.collections_data

    def create_collections(self, payload):
        key = f"COL{len(self.collections_data) + 1}"
        self.collections_data.append({"key": key, "data": {"name": payload[0]["name"]}})
        return {"success": {"0": key}}

    def items(self, **kwargs):
        return self.items_data

    def item_template(self, item_type, linkmode=None):
        template = {
            "itemType": item_type,
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
        if item_type == "attachment":
            template["linkMode"] = linkmode
        return template

    def create_items(self, payload, parentid=None):
        key = f"ITEM{len(self.items_data) + 1}"
        item = dict(payload[0])
        if parentid:
            item["parentItem"] = parentid
        self.created_items.append(item)
        self.items_data.append({"key": key, "version": 1, "data": item})
        return {"success": {"0": key}}

    def addto_collection(self, collection_key, item):
        self.added_to_collection.append((collection_key, item["key"]))
        item["data"]["collections"].append(collection_key)
        return True

    def children(self, item_key):
        return []

    def attachment_both(self, files, parentid=None):
        self.attachments.append((files, parentid))
        return {"success": [{"key": "ATTACH1"}]}


def test_build_add_to_zotero_issue_url_round_trips_payload():
    paper = make_sample_paper(
        title="A Useful Paper",
        url="https://arxiv.org/abs/2606.00005v1",
        pdf_url="https://arxiv.org/pdf/2606.00005",
    )
    url = build_add_to_zotero_issue_url(
        paper,
        "https://github.com/LiaojunChen/zotero-arxiv-daily",
        "每日论文推送",
        upload_pdf=False,
        attachment_mode="linked_url",
    )

    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    payload = parse_issue_body(query["body"][0])

    assert parsed.netloc == "github.com"
    assert parsed.path == "/LiaojunChen/zotero-arxiv-daily/issues/new"
    assert query["title"][0].startswith("[Add to Zotero]")
    assert payload.url == "https://arxiv.org/abs/2606.00005v1"
    assert payload.collection_name == "每日论文推送"
    assert payload.attachment_mode == "linked_url"
    assert payload.upload_pdf is False


def test_extract_arxiv_id_normalizes_versions_and_pdf_urls():
    assert extract_arxiv_id("https://arxiv.org/abs/2606.00005v1") == "2606.00005"
    assert extract_arxiv_id("https://arxiv.org/pdf/2606.00005.pdf") == "2606.00005"


def test_import_paper_to_zotero_creates_collection_and_item(monkeypatch):
    paper = PaperMetadata(
        source="arxiv",
        title="A Useful Paper",
        authors=["Ada Lovelace", "Grace Hopper"],
        abstract="A useful abstract.",
        url="https://arxiv.org/abs/2606.00005",
        pdf_url=None,
        arxiv_id="2606.00005",
        published="2026-06-01",
    )
    monkeypatch.setattr(zotero_import, "fetch_paper_metadata", lambda payload: paper)
    zot = StubZotero()

    result = import_paper_to_zotero(
        zot,
        ImportPayload(url="https://arxiv.org/abs/2606.00005", collection_name="每日论文推送", upload_pdf=False),
    )

    assert result["status"] == "created"
    assert result["collection_name"] == "每日论文推送"
    assert zot.collections_data[0]["data"]["name"] == "每日论文推送"
    assert zot.created_items[0]["title"] == "A Useful Paper"
    assert zot.created_items[0]["collections"] == ["COL1"]
    assert zot.created_items[0]["archiveID"] == "2606.00005"


def test_import_paper_to_zotero_reuses_existing_item(monkeypatch):
    paper = PaperMetadata(
        source="arxiv",
        title="A Useful Paper",
        authors=[],
        abstract="",
        url="https://arxiv.org/abs/2606.00005",
        pdf_url=None,
        arxiv_id="2606.00005",
    )
    monkeypatch.setattr(zotero_import, "fetch_paper_metadata", lambda payload: paper)
    zot = StubZotero()
    zot.collections_data.append({"key": "COL1", "data": {"name": "每日论文推送"}})
    zot.items_data.append(
        {
            "key": "ITEM1",
            "version": 1,
            "data": {
                "itemType": "preprint",
                "title": "A Useful Paper",
                "url": "https://arxiv.org/abs/2606.00005",
                "extra": "arXiv: 2606.00005",
                "collections": [],
            },
        }
    )

    result = import_paper_to_zotero(
        zot,
        ImportPayload(url="https://arxiv.org/abs/2606.00005", collection_name="每日论文推送", upload_pdf=False),
    )

    assert result["status"] == "existing"
    assert zot.created_items == []
    assert zot.added_to_collection == [("COL1", "ITEM1")]


def test_import_paper_to_zotero_reuses_item_missing_collections(monkeypatch):
    paper = PaperMetadata(
        source="arxiv",
        title="A Useful Paper",
        authors=[],
        abstract="",
        url="https://arxiv.org/abs/2606.00005",
        pdf_url=None,
        arxiv_id="2606.00005",
    )
    monkeypatch.setattr(zotero_import, "fetch_paper_metadata", lambda payload: paper)
    zot = StubZotero()
    zot.collections_data.append({"key": "COL1", "data": {"name": "每日论文推送"}})
    zot.items_data.append(
        {
            "key": "ITEM1",
            "version": 1,
            "data": {
                "itemType": "preprint",
                "title": "A Useful Paper",
                "url": "https://arxiv.org/abs/2606.00005",
                "extra": "arXiv: 2606.00005",
            },
        }
    )

    result = import_paper_to_zotero(
        zot,
        ImportPayload(url="https://arxiv.org/abs/2606.00005", collection_name="每日论文推送", upload_pdf=False),
    )

    assert result["status"] == "existing"
    assert zot.added_to_collection == [("COL1", "ITEM1")]
    assert zot.items_data[0]["data"]["collections"] == ["COL1"]


def test_import_paper_to_zotero_uses_parent_when_search_matches_attachment(monkeypatch):
    paper = PaperMetadata(
        source="arxiv",
        title="A Useful Paper",
        authors=[],
        abstract="",
        url="https://arxiv.org/abs/2606.00005",
        pdf_url=None,
        arxiv_id="2606.00005",
    )
    monkeypatch.setattr(zotero_import, "fetch_paper_metadata", lambda payload: paper)
    zot = StubZotero()
    zot.collections_data.append({"key": "COL1", "data": {"name": "每日论文推送"}})
    parent = {
        "key": "ITEM1",
        "version": 1,
        "data": {
            "itemType": "preprint",
            "title": "A Useful Paper",
            "url": "https://arxiv.org/abs/2606.00005",
            "extra": "arXiv: 2606.00005",
        },
    }
    attachment = {
        "key": "ATTACH1",
        "version": 1,
        "data": {
            "itemType": "attachment",
            "title": "A Useful Paper PDF",
            "url": "https://arxiv.org/pdf/2606.00005",
            "parentItem": "ITEM1",
        },
    }
    zot.items = lambda **kwargs: [attachment]
    zot.item = lambda key: parent

    result = import_paper_to_zotero(
        zot,
        ImportPayload(url="https://arxiv.org/abs/2606.00005", collection_name="每日论文推送", upload_pdf=False),
    )

    assert result["status"] == "existing"
    assert result["item_key"] == "ITEM1"
    assert zot.created_items == []
    assert zot.added_to_collection == [("COL1", "ITEM1")]
    assert parent["data"]["collections"] == ["COL1"]


def test_override_attachment_options_forces_linked_url_for_old_issue_payload():
    payload = import_payload_from_dict(
        {
            "source": "arxiv",
            "url": "https://arxiv.org/abs/2606.00005",
            "pdf_url": "https://arxiv.org/pdf/2606.00005",
            "collection_name": "每日论文推送",
            "upload_pdf": True,
        }
    )

    overridden = override_attachment_options(payload, attachment_mode="linked_url", upload_pdf="false")

    assert payload.attachment_mode == "upload"
    assert payload.upload_pdf is True
    assert overridden.attachment_mode == "linked_url"
    assert overridden.upload_pdf is False


def test_import_paper_to_zotero_creates_linked_pdf_attachment(monkeypatch):
    paper = PaperMetadata(
        source="arxiv",
        title="A Useful Paper",
        authors=[],
        abstract="",
        url="https://arxiv.org/abs/2606.00005",
        pdf_url="https://arxiv.org/pdf/2606.00005",
        arxiv_id="2606.00005",
    )
    monkeypatch.setattr(zotero_import, "fetch_paper_metadata", lambda payload: paper)
    zot = StubZotero()

    result = import_paper_to_zotero(
        zot,
        ImportPayload(
            url="https://arxiv.org/abs/2606.00005",
            collection_name="每日论文推送",
            attachment_mode="linked_url",
            upload_pdf=False,
        ),
    )

    assert result["pdf_status"] == "linked_url"
    attachment = zot.created_items[-1]
    assert attachment["itemType"] == "attachment"
    assert attachment["linkMode"] == "linked_url"
    assert attachment["url"] == "https://arxiv.org/pdf/2606.00005"
    assert attachment["parentItem"] == "ITEM1"
