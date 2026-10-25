import json
import os
import re
import time
import urllib.parse
from playwright.sync_api import sync_playwright
import requests

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")
SEEN_FILE = "amd_seen.json"
INIT_FLAG = "PROMO_KEYS_V14_STEAMDB_ART"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_set):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(list(seen_set)), f, indent=2)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def clean_game_title(raw_title):
    """Strips giveaway suffix words so Steam search finds the exact game."""
    title = raw_title
    if " - " in title:
        title = title.split(" - ")[0]

    patterns = [
        r"\bnaval supplies\b.*$",
        r"\bbonus code\b.*$",
        r"\bgame key\b.*$",
        r"\bkey giveaway\b.*$",
        r"\bgiveaway\b.*$",
        r"\bsweepstakes\b.*$",
        r"\bpack\b.*$",
        r"\bbundle\b.*$",
        r"\bcode\b.*$",
        r"\bdlc\b.*$",
    ]
    for pat in patterns:
        title = re.sub(pat, "", title, flags=re.I).strip()

    return title.strip(" -:!|") or raw_title


def get_steamdb_artwork(raw_title):
    """Queries Steam's database (used by SteamDB) for the game's official widescreen header banner."""
    game_name = clean_game_title(raw_title)
    print(f"Querying Steam/SteamDB artwork for: '{game_name}' (Original: '{raw_title}')")

    try:
        query = urllib.parse.quote(game_name)
        url = f"https://store.steampowered.com/api/storesearch/?term={query}&l=english&cc=US"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                app_id = items[0].get("id")
                for item in items:
                    if item.get("name", "").lower() == game_name.lower():
                        app_id = item.get("id")
                        break

                if app_id:
                    # Query appdetails for exact hashed header image URL used by SteamDB
                    details_url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=english"
                    d_resp = requests.get(details_url, headers=HEADERS, timeout=8)
                    if d_resp.status_code == 200:
                        app_data = d_resp.json().get(str(app_id), {}).get("data", {})
                        header_img = app_data.get("header_image")
                        if header_img:
                            return header_img, app_id

                    # Fallback CDN header URL
                    header_url = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg"
                    return header_url, app_id
    except Exception as e:
        print(f"SteamDB artwork lookup error for '{game_name}': {e}")

    return None, None


def send_discord_card(
    title,
    url,
    keys_text,
    image_url=None,
    steam_app_id=None,
    source="Promotions Page",
    ping=False,
):
    if not WEBHOOK_URL:
        print("CRITICAL ERROR: DISCORD_WEBHOOK secret is empty or missing!")
        return

    is_empty = "0 keys" in keys_text.lower()
    color = 9807270 if is_empty else 15548997

    steamdb_line = (
        f"📊 **[View on SteamDB](https://steamdb.info/app/{steam_app_id}/)**\n"
        if steam_app_id
        else ""
    )

    embed = {
        "title": f"🎮 {title}",
        "url": url,
        "description": (
            f"🔑 **Key Status:** `{keys_text}`\n"
            f"👉 **[CLICK HERE TO CLAIM]({url})**\n"
            f"{steamdb_line}\n"
            f"📡 *Detected via: **{source}***"
        ),
        "color": color,
    }

    if image_url and str(image_url).startswith("http"):
        embed["image"] = {"url": image_url}

    payload = {
        "username": "AMD Key Drop Tracker",
        "embeds": [embed],
    }
    if ping and not is_empty:
        payload["content"] = (
            f"@everyone 🚨 **NEW AMD GAME KEY DROP: {title} ({keys_text})**"
        )

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        print(f"Discord Webhook Response for '{title}': HTTP {r.status_code}")
        time.sleep(1.5)
    except Exception as e:
        print(f"Webhook delivery failed: {e}")


def check_amd_promotions():
    seen = load_seen()
    is_manual = EVENT_NAME == "workflow_dispatch"
    first_run = INIT_FLAG not in seen
    updated = False
    cards_sent = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1920, "height": 1080},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        collected_items = []

        # ---------------------------------------------------------
        # 1. EARLY DETECTION: Forum JSON API
        # ---------------------------------------------------------
        try:
            print("Checking AMD Forum API for early drops...")
            resp = page.goto(
                "https://www.amdgaming.com/latest.json", timeout=20000
            )
            if resp and resp.status == 200:
                data = json.loads(page.inner_text("body"))
                topics = data.get("topic_list", {}).get("topics", [])
                topics.reverse()  # Oldest first

                for topic in topics:
                    title = topic.get("title", "")
                    slug = topic.get("slug", "")
                    topic_id = topic.get("id", "")
                    fallback_img = topic.get("image_url")
                    closed = topic.get("closed", False)

                    if any(
                        k in title.lower()
                        for k in [
                            "giveaway",
                            "key",
                            "drop",
                            "sweepstakes",
                            "free",
                            "claim",
                        ]
                    ):
                        if "historical" in title.lower() or "winner" in title.lower():
                            continue

                        topic_url = f"https://www.amdgaming.com/t/{slug}/{topic_id}"
                        track_key = f"FORUM::{topic_id}"
                        status = "🔴 Closed" if closed else "🟢 Early Forum Drop"

                        steam_art, app_id = get_steamdb_artwork(title)
                        final_img = steam_art or fallback_img

                        if track_key not in seen:
                            collected_items.append({
                                "title": title,
                                "url": topic_url,
                                "keys": status,
                                "img": final_img,
                                "app_id": app_id,
                                "source": "AMD Forum API (Early Detection)",
                                "track_key": track_key,
                                "ping": (not first_run and not closed)
                            })
        except Exception as e:
            print(f"Forum API check error: {e}")

        # ---------------------------------------------------------
        # 2. LIVE PROMOTIONS PAGE: Tabs & Remaining Keys
        # ---------------------------------------------------------
        try:
            print("Opening https://www.amdgaming.com/promotions ...")
            page.goto(
                "https://www.amdgaming.com/promotions",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(6000)

            body_text = page.inner_text("body")
            lines = [
                line.strip() for line in body_text.split("\n") if line.strip()
            ]
            sidebar_tabs = []
            for line in lines:
                if (
                    re.search(r"Giveaway!?$", line, re.I)
                    and 8 < len(line) < 75
                    and "featured giveaway" not in line.lower()
                    and "exclusive giveaways" not in line.lower()
                    and line not in sidebar_tabs
                ):
                    sidebar_tabs.append(line)

            cleaned_tabs = []
            for t in sidebar_tabs:
                norm = re.sub(r"[^a-z0-9]", "", t.lower())
                if not any(
                    re.sub(r"[^a-z0-9]", "", existing.lower()) == norm
                    for existing in cleaned_tabs
                ):
                    cleaned_tabs.append(t)

            cleaned_tabs.reverse()  # Oldest/top items processed first
            print(f"Detected Giveaway Tabs (Reverse Order): {cleaned_tabs}")

            for tab_name in cleaned_tabs:
                try:
                    page.evaluate(
                        """(targetText) => {
                            const els = Array.from(document.querySelectorAll('*'));
                            const match = els.find(el => el.children.length === 0 && el.textContent.trim() === targetText);
                            if (match) match.click();
                        }""",
                        tab_name,
                    )
                    page.wait_for_timeout(3500)

                    current_text = page.inner_text("body")
                    key_match = re.search(
                        r"([\d,]+\s+Keys?\s+Remaining)", current_text, re.I
                    )
                    keys_str = (
                        f"🟢 {key_match.group(1)}"
                        if key_match
                        else "🟢 Keys Available"
                    )
                    if key_match and key_match.group(1).strip().startswith("0"):
                        keys_str = "🔴 0 Keys Remaining"

                    # Fetch official SteamDB artwork + Steam App ID link
                    steam_art, app_id = get_steamdb_artwork(tab_name)

                    slug = slugify(tab_name)
                    track_key = f"TAB::{slug}"

                    if track_key not in seen or first_run or is_manual:
                        collected_items.append({
                            "title": tab_name,
                            "url": "https://www.amdgaming.com/promotions",
                            "keys": keys_str,
                            "img": steam_art,
                            "app_id": app_id,
                            "source": "AMD Promotions Live Page",
                            "track_key": track_key,
                            "ping": (track_key not in seen and not first_run)
                        })
                except Exception as tab_e:
                    print(f"Tab error for {tab_name}: {tab_e}")

        except Exception as e:
            print(f"Promotions scan error: {e}")

        browser.close()

    # Post collected items to Discord (Oldest at top, latest/early at bottom)
    for item in collected_items:
        send_discord_card(
            title=item["title"],
            url=item["url"],
            keys_text=item["keys"],
            image_url=item["img"],
            steam_app_id=item.get("app_id"),
            source=item["source"],
            ping=item["ping"],
        )
        seen.add(item["track_key"])
        updated = True
        cards_sent += 1

    if first_run and cards_sent > 0:
        seen.add(INIT_FLAG)
        updated = True

    if updated:
        save_seen(seen)


if __name__ == "__main__":
    check_amd_promotions()
