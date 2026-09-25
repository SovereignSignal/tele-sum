"""Parse a t.me/s/<channel> preview HTML into importer JSONL on stdout.

One row per post: {"telegram_id": int, "text": str, "url": str|null, "posted_at": iso8601|null}
Usage: python3 scripts/parse_tme_preview.py /tmp/preview.html > /tmp/repos_posts.jsonl

Source the HTML first with: curl -s "https://t.me/s/<channel>" -o /tmp/preview.html
A single preview page returns only the most recent ~20 posts; for deeper history
page back with the ?before=<oldest_id> query param and concatenate the JSONL.
"""
import json
import sys

from bs4 import BeautifulSoup


def parse_preview(html: str):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for w in soup.select(".tgme_widget_message"):
        # skip Telegram service messages (channel created, photo updated, auto-delete
        # notices) — they carry a service_message class and are not content posts.
        if "service_message" in (w.get("class") or []):
            continue
        post = w.get("data-post", "")                  # "<channel>/<id>"
        tid = post.rsplit("/", 1)[1] if "/" in post else ""
        if not tid.isdigit():
            continue
        text_el = w.select_one(".tgme_widget_message_text")
        text = text_el.get_text("\n", strip=True) if text_el else ""
        if not text.strip():
            continue
        links = [{"text": a.get_text(" ", strip=True), "url": a["href"]}
                 for a in text_el.select("a[href]")
                 if a["href"].startswith(("https://", "http://"))] if text_el else []
        time_el = w.select_one("time[datetime]")
        rows.append({
            "telegram_id": int(tid),
            "text": text,
            "url": links[0]["url"] if links else None,
            "entity_links": links,
            "posted_at": time_el["datetime"] if time_el else None,
        })
    return rows


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as f:
        for row in parse_preview(f.read()):
            print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
