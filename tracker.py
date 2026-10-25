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
INIT_FLAG = "PROMO_STRICT_CURRENT_V1"

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
                    details_url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=english"
                    d_resp = requests.get(details_url, headers=HEADERS, timeout=8)
                    if d_resp.status_code == 200:
                        app_data = d_resp.json().get(str(app_id), {}).get("data", {})
                        header_img = app_data.get("header_image")
                        if header_img:
                            return header_img, app_id

                    header_url = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg"
                    return header_url, app_id
    except Exception as e:
        print(f"SteamDB artwork lookup error for '{game_name}': {e}")

    return None, None


def send_discord_card(
    title,
    url,
    image_url=None,
    steam_app_id=None,
    source="Promotions Page",
    is_early=False,
):
    if not WEBHOOK_URL:
        print("CRITICAL ERROR: DISCORD_WEBHOOK secret is empty or missing!")
        return

    steamdb_line = (
        f"📊 **[View on SteamDB](https://steamdb.info/app/{steam_app_id}/)**\n"
        if steam_app_id
        else ""
    )

    status_label = (
        "⚡ **Status:** `Early / Unannounced Drop (Not yet public)`"
        if is_early
        else "🟢 **Status:** `Active Live Giveaway`"
    )

    embed = {
        "title": f"🎮 {title}",
        "url": url,
        "description": (
            f"{status_label}\n"
            f"👉 **[CLICK HERE TO CLAIM]({url})**\n"
            f"{steamdb_line}\n"
            f"📡 *Detected via: **{source}***"
        ),
        "color": 16734296 if is_early else 15548997,
    }

    if image_url and str(image_url).startswith("http"):
        embed["image"] = {"url": image_url}

    tag_name = "EARLY UPCOMING DROP"
    payload = {
        "username": "AMD Early Drop Monitor",
        "embeds": [embed],
        "content": f"@everyone 🔥 **{tag_name}: {title}**",
    }

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

        # ---------------------------------------------------------
        # 1. EARLY DETECTION: Sitemap XML for unlisted upcoming links
        # ---------------------------------------------------------
        try:
            print("Checking Sitemap XML for early unlisted promotion links...")
            resp = page.goto("https://www.amdgaming.com/sitemap.xml", timeout=20000)
            if resp and resp.status == 200:
                xml_text = page.content()
                early_urls = re.findall(
                    r"https://www\.amdgaming\.com/promotions/[a-zA-Z0-9-_]+",
                    xml_text,
                )
                for promo_url in set(early_urls):
                    if "terms" not in promo_url and promo_url not in seen:
                        name = promo_url.split("/")[-1].replace("-", " ").title()
                        steam_art, app_id = get_steamdb_artwork(name)
                        
                        if not first_run or is_manual:
                            send_discord_card(
                                title=name,
                                url=promo_url,
                                image_url=steam_art,
                                steam_app_id=app_id,
                                source="Sitemap XML (Early Unannounced Detection)",
                                is_early=True,
                            )
                        seen.add(promo_url)
                        updated = True
        except Exception as e:
            print(f"Sitemap Error: {e}")

        # ---------------------------------------------------------
        # 2. LIVE PROMOTIONS PAGE: Strictly target current sidebar buttons/tabs
        # ---------------------------------------------------------
        try:
            print("Opening https://www.amdgaming.com/promotions ...")
            page.goto(
                "https://www.amdgaming.com/promotions",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(6000)

            # Use strict JavaScript selector to pull ONLY clickable interactive sidebar tabs or promo cards
            active_tabs = page.evaluate("""() => {
                const results = [];
                // Target active navigation buttons or promotional items specifically
                const items = document.querySelectorAll('button, a[href*="promotions"], .promo-nav-item, [role="tab"]');
                items.forEach(el => {
                    const text = el.innerText.trim();
                    if (text && text.length > 5 && text.length < 60 && /giveaway/i.test(text)) {
                        if (!text.toLowerCase().includes('featured') && !text.toLowerCase().includes('exclusive')) {
                            results.push(text);
                        }
                    }
                });
                return [...new Set(results)];
            }""")

            print(f"Strictly Detected Active Giveaway Tabs: {active_tabs}")

            for tab_name in active_tabs:
                try:
                    steam_art, app_id = get_steamdb_artwork(tab_name)
                    slug = slugify(tab_name)
                    track_key = f"TAB::{slug}"

                    if track_key not in seen:
                        if not first_run or is_manual:
                            send_discord_card(
                                title=tab_name,
                                url="https://www.amdgaming.com/promotions",
                                image_url=steam_art,
                                steam_app_id=app_id,
                                source="AMD Promotions Page",
                                is_early=False,
                            )
                        seen.add(track_key)
                        updated = True
                except Exception as tab_e:
                    print(f"Tab error for {tab_name}: {tab_e}")

        except Exception as e:
            print(f"Promotions scan error: {e}")

        browser.close()

    if first_run:
        seen.add(INIT_FLAG)
        updated = True

    if updated:
        save_seen(seen)


if __name__ == "__main__":
    check_amd_promotions()
