"""Scrape HYROX races + Vivenu ticket data, diff against previous state, notify via Telegram."""
import html as html_mod
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
ROOT = Path(__file__).parent
LIST_URL = "https://hyrox.com/find-my-race/"
MENS_OPEN_RE = re.compile(r"^HYROX MEN\s*\|", re.IGNORECASE)


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def parse_race_list(html: str) -> list[dict]:
    cards = re.split(r'(?=<article class="w-grid-item[^"]*event)', html)
    out = []
    for c in cards:
        if "event_city_letter_code" not in c:
            continue
        code = re.search(r'event_city_letter_code[^>]*>\s*<span[^>]*>([^<]+)</span>', c)
        title = re.search(r'<a href="(https://hyrox\.com/event/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>', c)
        btns = re.findall(r'<span class="w-btn-label">([^<]+)</span>', c)
        if not (code and title):
            continue
        out.append({
            "code": code.group(1).strip(),
            "title": html_mod.unescape(title.group(2).strip()),
            "link": title.group(1).strip(),
            "slug": title.group(1).strip().rstrip("/").split("/")[-1],
            "on_sale_button": "Buy Tickets" in btns,
        })
    return out


def find_vivenu_url(event_html: str) -> str | None:
    # Hyrox uses regional subdomains (hk.hyrox.com, fr.hyrox.com, etc.) for Vivenu shops.
    m = re.search(
        r'https://(?!www\.)[a-z]{2,5}\.hyrox\.com/event/[a-z0-9\-]+(?:\?[^"\']*)?',
        event_html,
    )
    return m.group(0) if m else None


def parse_race_dates(event_html: str) -> str | None:
    text = re.sub(r"<[^>]+>", " ", event_html)
    text = re.sub(r"\s+", " ", text)
    m = re.search(
        r"(\d{1,2})\.\s*([A-Za-z]+)\.\s*(\d{4})\s*[–—\-]\s*(\d{1,2})\.\s*([A-Za-z]+)\.\s*(\d{4})",
        text,
    )
    if not m:
        return None
    return f"{m.group(1)} {m.group(2)} {m.group(3)} – {m.group(4)} {m.group(5)} {m.group(6)}"


def parse_vivenu(html: str) -> dict | None:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    page_props = data.get("props", {}).get("pageProps", {})
    shop = page_props.get("shop") or {}
    tickets = shop.get("tickets", [])
    mens_open_tickets = [t for t in tickets if MENS_OPEN_RE.match(t.get("name", ""))]
    return {
        "sale_status": shop.get("saleStatus"),
        "sell_start": shop.get("sellStart"),
        "sell_end": shop.get("sellEnd"),
        "mens_open_exists": len(mens_open_tickets) > 0,
        "mens_open_active": any(t.get("active") for t in mens_open_tickets),
        "mens_open_tickets": [
            {"name": t.get("name"), "active": t.get("active"), "price": t.get("price")}
            for t in mens_open_tickets
        ],
    }


def days_until(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt - datetime.now(timezone.utc)).days


def derive_status(r: dict) -> None:
    """Compute `status` and `mens_open_buyable` from raw signals.

    Status priority: Vivenu `saleStatus` > listing "Buy Tickets" button presence.
    The listing button can flip to "Buy Tickets" before the Vivenu shop is published,
    so it's used only as a fallback signal (status="imminent").
    """
    sale_status = r.get("sale_status")
    on_sale_button = r.get("on_sale_button")

    if sale_status == "onSale":
        r["status"] = "on_sale"
    elif sale_status == "planned":
        r["status"] = "planned"
        r["drops_in_days"] = days_until(r.get("sell_start"))
    elif sale_status == "ended":
        r["status"] = "ended"
    elif on_sale_button and not sale_status:
        # Listing button is "Buy Tickets" but Vivenu shop not published yet.
        r["status"] = "imminent"
    elif sale_status:
        r["status"] = sale_status  # unknown vivenu state, pass through
    else:
        r["status"] = "pending"

    r["mens_open_buyable"] = (sale_status == "onSale") and bool(r.get("mens_open_active"))


def collect() -> dict:
    config = json.loads((ROOT / "config.json").read_text())
    favorites = config["favorites"]

    print(f"Fetching race list from {LIST_URL}")
    list_html = fetch(LIST_URL)
    all_races = parse_race_list(list_html)
    by_slug = {r["slug"]: r for r in all_races}
    print(f"  parsed {len(all_races)} races")

    results = []
    for fav in favorites:
        slug, label = fav["slug"], fav["label"]
        print(f"\nProcessing {label} ({slug})")
        race = by_slug.get(slug)
        if not race:
            print(f"  ! not found in race list")
            results.append({"slug": slug, "label": label, "error": "not_found_in_list"})
            continue

        result = {
            "slug": slug,
            "label": label,
            "title": race["title"],
            "link": race["link"],
            "city_code": race["code"],
            "on_sale_button": race["on_sale_button"],
        }

        try:
            event_html = fetch(race["link"])
        except Exception as e:
            print(f"  ! event fetch failed: {e}")
            result["error"] = f"event_fetch_failed: {e}"
            results.append(result)
            continue

        result["race_dates"] = parse_race_dates(event_html)
        if result["race_dates"]:
            print(f"  race dates: {result['race_dates']}")

        vivenu_url = find_vivenu_url(event_html)
        if not vivenu_url:
            print(f"  no vivenu link yet (tickets pending)")
            result["vivenu_status"] = "no_vivenu_link"
            results.append(result)
            continue

        result["vivenu_url"] = vivenu_url
        try:
            vivenu_html = fetch(vivenu_url)
            vd = parse_vivenu(vivenu_html)
            if vd:
                result.update(vd)
                print(f"  saleStatus={vd['sale_status']} mens_open_active={vd['mens_open_active']}")
            else:
                result["vivenu_status"] = "no_next_data"
        except Exception as e:
            print(f"  ! vivenu fetch failed: {e}")
            result["vivenu_status"] = f"vivenu_fetch_failed: {e}"

        results.append(result)
        time.sleep(1)  # be polite

    for r in results:
        derive_status(r)

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_races_on_site": len(all_races),
        "races": results,
    }


def diff_messages(old: dict, new: dict) -> list[str]:
    old_by_slug = {r["slug"]: r for r in old.get("races", [])}
    msgs = []
    for r in new["races"]:
        prev = old_by_slug.get(r["slug"])
        if prev is None:
            continue  # don't notify on first-time-seeing-this-favorite

        # 1. Listing button flipped to Buy Tickets (early signal — shop may not be live yet)
        if not prev.get("on_sale_button") and r.get("on_sale_button"):
            link = r.get("vivenu_url") or r.get("link", "")
            msgs.append(f"[IMMINENT] {r['label']}: Buy Tickets button just appeared on the listing\n{link}")

        # 2. Vivenu shop opened — the real "tickets dropped" event
        if prev.get("status") != "on_sale" and r.get("status") == "on_sale":
            link = r.get("vivenu_url") or r.get("link", "")
            close_days = days_until(r.get("sell_end"))
            tail = f" — sales close in {close_days} days" if close_days is not None else ""
            msgs.append(f"[ON SALE] {r['label']}: Vivenu shop is open{tail}\n{link}")

        # 3. Men's Open became buyable (shop on sale AND ticket active)
        if not prev.get("mens_open_buyable") and r.get("mens_open_buyable"):
            link = r.get("vivenu_url") or r.get("link", "")
            msgs.append(f"[MEN'S OPEN AVAILABLE] {r['label']}: HYROX MEN is buyable now\n{link}")

        # 4. Men's Open became unbuyable (likely sold out, since once on sale rarely flips back)
        if prev.get("mens_open_buyable") and not r.get("mens_open_buyable"):
            msgs.append(f"[MEN'S OPEN UNAVAILABLE] {r['label']}: HYROX MEN just went off sale (sold out?)")

    # De-duplicate while preserving order
    seen = set()
    out = []
    for m in msgs:
        if m not in seen:
            out.append(m)
            seen.add(m)
    return out


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(
        url,
        data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "false"},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"  telegram error {r.status_code}: {r.text[:200]}")


def update_google_sheet(data: dict) -> None:
    url = os.environ.get("GOOGLE_SHEETS_WEBAPP_URL")
    secret = os.environ.get("GOOGLE_SHEETS_SECRET")
    if not (url and secret):
        return
    payload = {
        "secret": secret,
        "updated_at": data["updated_at"],
        "races": data["races"],
    }
    try:
        # Apps Script web apps may 302-redirect to googleusercontent.com; requests
        # follows it (downgrades to GET) but the original POST has already been
        # processed by the script before the redirect.
        r = requests.post(url, json=payload, timeout=30)
        if 200 <= r.status_code < 300:
            print(f"Google Sheet updated: {r.text[:120]}")
        else:
            print(f"Google Sheet error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"Google Sheet exception: {e}")


def main() -> int:
    out = collect()

    state_path = ROOT / "state.json"
    data_path = ROOT / "data.json"
    old = {}
    if state_path.exists():
        try:
            old = json.loads(state_path.read_text())
        except Exception:
            old = {}

    msgs = diff_messages(old, out)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if msgs:
        print(f"\n{len(msgs)} change(s) detected:")
        for m in msgs:
            print(f"  - {m.splitlines()[0]}")
        if token and chat_id:
            for m in msgs:
                send_telegram(token, chat_id, m)
        else:
            print("  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping notifications")
    else:
        print("\nNo changes since last run.")

    update_google_sheet(out)

    state_path.write_text(json.dumps(out, indent=2, sort_keys=True))
    data_path.write_text(json.dumps(out, indent=2, sort_keys=True))
    print(f"Wrote {data_path.name} and {state_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
