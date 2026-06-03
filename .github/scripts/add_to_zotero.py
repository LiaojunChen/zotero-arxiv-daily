import json
import os
import sys

from pyzotero import zotero

from zotero_arxiv_daily.zotero_import import (
    DEFAULT_COLLECTION_NAME,
    import_paper_to_zotero,
    import_payload_from_dict,
    parse_bool,
    parse_issue_body,
    write_github_output,
)


def main() -> int:
    if os.environ.get("ISSUE_BODY"):
        payload = parse_issue_body(os.environ["ISSUE_BODY"])
    else:
        payload = import_payload_from_dict(
            {
                "source": "arxiv",
                "url": os.environ.get("PAPER_URL"),
                "collection_name": os.environ.get("COLLECTION_NAME") or DEFAULT_COLLECTION_NAME,
                "upload_pdf": parse_bool(os.environ.get("UPLOAD_PDF"), default=True),
            }
        )

    zot = zotero.Zotero(os.environ["ZOTERO_ID"], "user", os.environ["ZOTERO_KEY"])
    result = import_paper_to_zotero(zot, payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    write_github_output(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
