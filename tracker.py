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
INIT_FLAG = "PROMO_ACCURATE_KEYS_V1"

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
    """Strips giveaway suffix phrases while preserving the exact game title."""
    title = raw_title.strip()
    if " - " in title:
        title = title.split(" - ")[0].strip()

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
        r"\s+bonus code!?$",
    ]
    for pat in suffixes:
        title = re.sub(pat, "", title, flags=re.I).strip()

    return title.strip(" -:!|") or raw_title


def get_exact_steam_match(raw_title):
    manual_aliases = {
        "deadswitch combat": "Deadswitch Combat",
        "nutmeg": "NUTMEG! A Nostalgic Deckbuilding Football Manager",
        "a storied life tabitha": "A Storied Life: Tabitha",
    }
    
    clean_key = clean_game_title(raw_title).lower()
    search_query = manual_aliases.get(clean_key, clean_game_title(raw_title))

    try:
        q = urllib.parse.quote(search_query)
        search_url = f"https://store.steampowered.com/api/storesearch/?term={q}&l=english&cc=US"
        resp = requests.get(search_url, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                app_id = items[0].get("id")
                details_url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=english"
                d_resp = requests.get(details_url, headers=HEADERS, timeout=8)
                if d_resp.status_code == 200:
                    app_data = d_resp.json().get(str(app_id), {}).get("data", {})
                    header_img = app_data.get("header_image")
                    if header_img:
                        return header_img, app_id
                return (
                    f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg",
                    app_id,
                )
    except Exception as e:
        print(f"Steam lookup error for '{raw_title}': {e}")

    return None, None


def capture_amd_showcase_artwork(page):
    img_info = page.evaluate(
        """() => {
        const badKeywords = ['logo', 'ryzen', 'radeon', 'avatar', 'icon', 'svg', 'header', 'footer'];
        const isLogo = (str) => badKeywords.some(w => (str || '').toLowerCase().includes(w));

        let candidates = [];
        for (const img of document.querySelectorAll('img')) {
            const rect = img.getBoundingClientRect();
            const src = img.currentSrc || img.src || '';
            const alt = img.alt || '';
            if (
                rect.top > 250 &&
                rect.width >= 260 &&
                rect.height >= 130 &&
                rect.width / rect.height < 3.2 &&
                !isLogo(src) &&
                !isLogo(alt)
            ) {
                candidates.push({
                    src: src.startsWith('http') ? src : null,
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    area: rect.width * rect.height
                });
            }
        }
        candidates.sort((a, b) => b.area - a.area);
        return candidates.length > 0 ? candidates[0] : null;
    }"""
    )

    if img_info:
        try:
            clip_box = {
                "x": max(0, img_info["x"]),
                "y": max(0, img_info["y"]),
                "width": img_info["width"],
                "height": img_info["height"],
            }
            shot = page.screenshot(clip=clip_box)
            return img_info.get("src"), shot
        except Exception:
            if img_info.get("src"):
                return img_info["src"], None

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

    is_empty = "0 keys" in keys_text.lower() or "out of stock" in keys_text.lower()
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
        if screenshot_bytes:
            embed["image"] = {"url": "attachment://amd_promo_banner.png"}
            files = {
                "file": ("amd_promo_banner.png", screenshot_bytes, "image/png")
            }
            r = requests.post(
                WEBHOOK_URL,
                data={"payload_json": json.dumps(payload)},
                files=files,
                timeout=20,
            )
        elif image_url and str(image_url).startswith("http"):
            embed["image"] = {"url": image_url}
            r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        else:
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

    collected_items = []

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
        # 1. Sitemap XML (Early Drops)
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
                    r"https://www\.amdgaming\.com/promotions/[a-zA-Z0-9-_]*(?:giveaway|key)[a-zA-Z0-9-_]*",
                    xml_text,
                    re.I,
                )
                for promo_url in set(early_urls):
                    if promo_url not in seen:
                        name = promo_url.split("/")[-1].replace("-", " ").title()
                        steam_art, app_id = get_exact_steam_match(name)
                        collected_items.append({
                            "title": name,
                            "url": promo_url,
                            "keys_text": "⏳ Early Sitemap Link",
                            "image_url": steam_art,
                            "screenshot_bytes": None,
                            "steam_app_id": app_id,
                            "source": "Sitemap XML (Early Detection)",
                            "ping": not first_run,
                            "track_key": promo_url
                        })
        except Exception as e:
            print(f"Sitemap Error: {e}")

        # ---------------------------------------------------------
        # 2. Live Promotions Page Tabs & Precise Key Counter
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
                    page.wait_for_timeout(3000)

                    # Precise JS counter scanner that specifically looks for active number badges or remaining keywords
                    keys_str = page.evaluate("""() => {
                        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                        let node;
                        while (node = walker.nextNode()) {
                            const text = node.nodeValue.trim();
                            if (/\\d[\\d,]*\\s+Keys?\\s+(?:Remaining|Left)/i.test(text)) {
                                return text;
                            }
                        }
                        // Fallback check for general out-of-stock messages
                        const fullBody = document.body.innerText;
                        if (/out of keys|all keys claimed|ended/i.test(fullBody)) {
                            return "0 Keys Remaining (Out of Stock)";
                        }
                        return "Keys Available";
                    }""")

                    if keys_str and "Keys" in keys_str:
                        if keys_str.strip().lower().startswith("0") or "out of stock" in keys_str.lower():
                            keys_str = f"🔴 {keys_str}"
                        else:
                            keys_str = f"🟢 {keys_str}"
                    else:
                        keys_str = "🟢 Keys Available"

                    amd_img_url, amd_shot_bytes = capture_amd_showcase_artwork(page)
                    steam_art, app_id = get_exact_steam_match(tab_name)

                    slug = slugify(tab_name)
                    track_key = f"TAB::{slug}"

                    if track_key not in seen or first_run or is_manual:
                        collected_items.append({
                            "title": tab_name,
                            "url": "https://www.amdgaming.com/promotions",
                            "keys_text": keys_str,
                            "image_url": amd_img_url or steam_art,
                            "screenshot_bytes": amd_shot_bytes,
                            "steam_app_id": app_id,
                            "source": "AMD Promotions Live Page",
                            "ping": (track_key not in seen and not first_run),
                            "track_key": track_key
                        })
                except Exception as tab_e:
                    print(f"Tab error for {tab_name}: {tab_e}")

        except Exception as e:
            print(f"Promotions scan error: {e}")

        browser.close()

    # Reverse sort: older/current items post first (at top), brand-new items post last (at bottom)
    collected_items.reverse()

    for item in collected_items:
        send_discord_card(
            title=item["title"],
            url=item["url"],
            keys_text=item["keys_text"],
            image_url=item["image_url"],
            screenshot_bytes=item["screenshot_bytes"],
            steam_app_id=item["steam_app_id"],
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
