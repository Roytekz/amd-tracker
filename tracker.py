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
INIT_FLAG = "PROMO_KEYS_V7_STEAM_ART"


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
    # If there is a hyphen separator like "World of Warships - Naval Supplies Giveaway", take the first part
    if " - " in title:
        title = title.split(" - ")[0]

    # Remove common giveaway terms from the end of the title
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


def get_steam_artwork(raw_title):
    """Queries Steam's official database (used by SteamDB) for the game's high-res header banner."""
    game_name = clean_game_title(raw_title)
    print(f"Searching Steam database for artwork: '{game_name}' (from '{raw_title}')")

    try:
        query = urllib.parse.quote(game_name)
        url = f"https://store.steampowered.com/api/storesearch/?term={query}&l=english&cc=US"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                # Pick exact name match if available, otherwise take top search result
                app_id = items[0].get("id")
                for item in items:
                    if item.get("name", "").lower() == game_name.lower():
                        app_id = item.get("id")
                        break

                if app_id:
                    # Official SteamDB / Steam widescreen header banner URL
                    header_url = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg"
                    print(f"Found Steam AppID {app_id} -> {header_url}")
                    return header_url, app_id
    except Exception as e:
        print(f"Steam artwork lookup failed for '{game_name}': {e}")

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
            f"👉 **[CLICK HERE TO CLAIM KEY]({url})**\n"
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
    first_v7_run = INIT_FLAG not in seen
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        # ---------------------------------------------------------
        # 1. Check Sitemap XML for Early Unlisted /promotions/ Links
        # ---------------------------------------------------------
        try:
            resp = page.goto(
                "https://www.amdgaming.com/sitemap.xml",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            if resp and resp.status == 200:
                xml_text = page.content()
                early_urls = re.findall(
                    r"https://www\.amdgaming\.com/promotions/[a-zA-Z0-9-_]+",
                    xml_text,
                )
                for promo_url in set(early_urls):
                    if promo_url not in seen:
                        name = (
                            promo_url.split("/")[-1].replace("-", " ").title()
                        )
                        art_url, app_id = get_steam_artwork(name)
                        send_discord_card(
                            title=name,
                            url=promo_url,
                            keys_text="⏳ Early Sitemap Link",
                            image_url=art_url,
                            steam_app_id=app_id,
                            source="Sitemap XML (Early Detection)",
                            ping=not first_v7_run,
                        )
                        seen.add(promo_url)
                        updated = True
                        cards_sent += 1
        except Exception as e:
            print(f"Sitemap Error: {e}")

        # ---------------------------------------------------------
        # 2. Check Live Promotions Page Tabs & Remaining Keys
        # ---------------------------------------------------------
        try:
            print("Opening https://www.amdgaming.com/promotions ...")
            page.goto(
                "https://www.amdgaming.com/promotions",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(7000)

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

            print(f"Detected Unique Giveaway Tabs: {cleaned_tabs}")

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
                    page.wait_for_timeout(2500)

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

                    # Fetch official Steam/SteamDB artwork + SteamDB link
                    art_url, app_id = get_steam_artwork(tab_name)

                    slug = slugify(tab_name)
                    track_key = f"TAB::{slug}"

                    if track_key not in seen or first_v7_run or is_manual:
                        send_discord_card(
                            title=tab_name,
                            url="https://www.amdgaming.com/promotions",
                            keys_text=keys_str,
                            image_url=art_url,
                            steam_app_id=app_id,
                            source="AMD Promotions Live Page",
                            ping=(track_key not in seen and not first_v7_run),
                        )
                        seen.add(track_key)
                        updated = True
                        cards_sent += 1
                except Exception as tab_e:
                    print(f"Tab error for {tab_name}: {tab_e}")

        except Exception as e:
            print(f"Promotions scan error: {e}")

        browser.close()

    if first_v7_run and cards_sent > 0:
        seen.add(INIT_FLAG)
        updated = True

    if updated:
        save_seen(seen)


if __name__ == "__main__":
    check_amd_promotions()
