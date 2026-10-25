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
INIT_FLAG = "PROMO_KEYS_V8_MULTI_ART"

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
    """Safely removes AMD giveaway suffix phrases while keeping the full game name intact."""
    title = raw_title.strip()
    if " - " in title:
        title = title.split(" - ")[0].strip()

    # Ordered from most specific suffix to general suffix
    suffixes = [
        r"\s+naval supplies giveaway!?$",
        r"\s+bonus code key giveaway!?$",
        r"\s+game key giveaway!?$",
        r"\s+closed beta key giveaway!?$",
        r"\s+early access key giveaway!?$",
        r"\s+beta key giveaway!?$",
        r"\s+steam key giveaway!?$",
        r"\s+key giveaway!?$",
        r"\s+code giveaway!?$",
        r"\s+pack giveaway!?$",
        r"\s+bundle giveaway!?$",
        r"\s+giveaway!?$",
        r"\s+sweepstakes!?$",
        r"\s+bonus code!?$",
    ]
    for pat in suffixes:
        title = re.sub(pat, "", title, flags=re.I).strip()

    return title.strip(" -:!|") or raw_title


def is_valid_image(url):
    """Checks that an image URL actually exists (HTTP 200) and isn't a broken link."""
    if not url or not url.startswith("http"):
        return False
    try:
        r = requests.head(url, headers=HEADERS, timeout=6, allow_redirects=True)
        if r.status_code == 200:
            return True
        # Some CDNs block HEAD requests, fallback to lightweight GET stream
        r_get = requests.get(url, headers=HEADERS, timeout=6, stream=True)
        r_get.close()
        return r_get.status_code == 200
    except Exception:
        return False


def get_game_artwork(raw_title):
    """
    4-Layer Artwork Finder:
    1. Steam Store Search + Steam AppDetails API (catches hashed URLs for newer games like GOALS)
    2. Steam Alternate Search (if subtitle caused a miss)
    3. Bing Game Key Art Search (catches non-Steam games like Epic/EA/Standalone titles)
    """
    game_name = clean_game_title(raw_title)
    print(f"Fetching artwork for: '{game_name}' (Original: '{raw_title}')")

    # --- LAYER 1 & 2: Steam Official API (with AppDetails fallback for hashed URLs) ---
    search_variants = [game_name]
    if ":" in game_name:
        search_variants.append(game_name.split(":")[0].strip())

    for query_text in search_variants:
        try:
            q = urllib.parse.quote(query_text)
            search_url = f"https://store.steampowered.com/api/storesearch/?term={q}&l=english&cc=US"
            resp = requests.get(search_url, headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    # Prefer exact case-insensitive title match, else first result
                    chosen = items[0]
                    for item in items:
                        if item.get("name", "").strip().lower() == query_text.lower():
                            chosen = item
                            break

                    app_id = chosen.get("id")
                    if app_id:
                        # Query appdetails to get the exact hashed header_image URL
                        details_url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=english"
                        d_resp = requests.get(details_url, headers=HEADERS, timeout=10)
                        if d_resp.status_code == 200:
                            app_data = d_resp.json().get(str(app_id), {}).get("data", {})
                            header_img = app_data.get("header_image")
                            if header_img and is_valid_image(header_img):
                                print(f"[Steam AppDetails] Found verified banner for {query_text}: {header_img}")
                                return header_img, app_id

                        # Fallback to standard CDN URLs if appdetails is rate-limited
                        cdn_candidates = [
                            f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg",
                            f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg",
                            chosen.get("tiny_image", "").replace("capsule_sm_120", "header"),
                        ]
                        for c_url in cdn_candidates:
                            if is_valid_image(c_url):
                                print(f"[Steam CDN] Found verified banner for {query_text}: {c_url}")
                                return c_url, app_id
        except Exception as e:
            print(f"Steam lookup error for '{query_text}': {e}")

    # --- LAYER 3: Web Game Banner Search (For non-Steam / Standalone / Epic / Wargaming titles) ---
    try:
        search_q = urllib.parse.quote(f"{game_name} game official key art wallpaper")
        bing_url = f"https://www.bing.com/images/search?q={search_q}&qft=+filterui:aspect-wide"
        b_resp = requests.get(bing_url, headers=HEADERS, timeout=10)
        if b_resp.status_code == 200:
            # Extract direct high-res image URLs (murl) from search results
            murls = re.findall(r"murl&quot;:&quot;(https?://[^&]+?\.(?:jpg|jpeg|png))&quot;", b_resp.text)
            for img_candidate in murls[:5]:
                if is_valid_image(img_candidate):
                    print(f"[Web Art Search] Found verified banner for {game_name}: {img_candidate}")
                    return img_candidate, None
    except Exception as e:
        print(f"Web artwork search error for '{game_name}': {e}")

    return None, None


def send_discord_card(
    title,
    url,
    keys_text,
    image_url=None,
    screenshot_bytes=None,
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

    payload = {
        "username": "AMD Key Drop Tracker",
        "embeds": [embed],
    }
    if ping and not is_empty:
        payload["content"] = (
            f"@everyone 🚨 **NEW AMD GAME KEY DROP: {title} ({keys_text})**"
        )

    try:
        if image_url and str(image_url).startswith("http"):
            embed["image"] = {"url": image_url}
            r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        elif screenshot_bytes:
            embed["image"] = {"url": "attachment://game_banner.png"}
            files = {"file": ("game_banner.png", screenshot_bytes, "image/png")}
            r = requests.post(
                WEBHOOK_URL,
                data={"payload_json": json.dumps(payload)},
                files=files,
                timeout=20,
            )
        else:
            r = requests.post(WEBHOOK_URL, json=payload, timeout=15)

        print(f"Discord Webhook Response for '{title}': HTTP {r.status_code}")
        time.sleep(1.5)
    except Exception as e:
        print(f"Webhook delivery failed: {e}")


def check_amd_promotions():
    seen = load_seen()
    is_manual = EVENT_NAME == "workflow_dispatch"
    first_v8_run = INIT_FLAG not in seen
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
                        art_url, app_id = get_game_artwork(name)
                        send_discord_card(
                            title=name,
                            url=promo_url,
                            keys_text="⏳ Early Sitemap Link",
                            image_url=art_url,
                            steam_app_id=app_id,
                            source="Sitemap XML (Early Detection)",
                            ping=not first_v8_run,
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

                    # Fetch verified artwork via 4-layer search
                    art_url, app_id = get_game_artwork(tab_name)

                    # Layer 4 Fallback: Live cropped screenshot of the center showcase banner if all web lookups fail
                    shot_bytes = None
                    if not art_url:
                        shot_bytes = page.screenshot(
                            clip={"x": 430, "y": 440, "width": 640, "height": 360}
                        )

                    slug = slugify(tab_name)
                    track_key = f"TAB::{slug}"

                    if track_key not in seen or first_v8_run or is_manual:
                        send_discord_card(
                            title=tab_name,
                            url="https://www.amdgaming.com/promotions",
                            keys_text=keys_str,
                            image_url=art_url,
                            screenshot_bytes=shot_bytes,
                            steam_app_id=app_id,
                            source="AMD Promotions Live Page",
                            ping=(track_key not in seen and not first_v8_run),
                        )
                        seen.add(track_key)
                        updated = True
                        cards_sent += 1
                except Exception as tab_e:
                    print(f"Tab error for {tab_name}: {tab_e}")

        except Exception as e:
            print(f"Promotions scan error: {e}")

        browser.close()

    if first_v8_run and cards_sent > 0:
        seen.add(INIT_FLAG)
        updated = True

    if updated:
        save_seen(seen)


if __name__ == "__main__":
    check_amd_promotions()
