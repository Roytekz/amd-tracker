import json
import os
import re
import time
from playwright.sync_api import sync_playwright
import requests

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")
SEEN_FILE = "amd_seen.json"
INIT_FLAG = "PROMO_KEYS_ONLY_V4"


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


def send_discord_card(title, url, keys_text, image_url=None, source="Promotions Page", ping=True):
    if not WEBHOOK_URL:
        print("Missing DISCORD_WEBHOOK!")
        return

    is_empty = "0 keys" in keys_text.lower()
    color = 9807270 if is_empty else 15548997  # Grey if 0 keys, AMD Red if keys available

    embed = {
        "title": f"🎮 {title}",
        "url": url,
        "description": (
            f"🔑 **Key Status:** `{keys_text}`\n"
            f"👉 **[CLICK HERE TO CLAIM KEY]({url})**\n\n"
            f"📡 *Detected via: **{source}***"
        ),
        "color": color,
    }

    if image_url and image_url.startswith("http"):
        embed["image"] = {"url": image_url}

    payload = {
        "username": "AMD Key Drop Tracker",
        "embeds": [embed],
    }
    if ping and not is_empty:
        payload["content"] = f"@everyone 🚨 **AMD Game Key Drop: {title} ({keys_text})**"

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"Sent Discord card for '{title}': HTTP {r.status_code}")
        time.sleep(1.5)
    except Exception as e:
        print(f"Webhook error: {e}")


def check_amd_promotions():
    seen = load_seen()
    is_manual = EVENT_NAME == "workflow_dispatch"
    first_v4_run = INIT_FLAG not in seen
    updated = False
    cards_sent = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        # ---------------------------------------------------------
        # 1. EARLY DETECTION: Check Sitemap XML for hidden /promotions/ URLs
        # ---------------------------------------------------------
        try:
            print("Checking Sitemap for early /promotions/ links...")
            resp = page.goto("https://www.amdgaming.com/sitemap.xml", timeout=20000)
            if resp and resp.status == 200:
                xml_text = page.content()
                early_urls = re.findall(
                    r"https://www\.amdgaming\.com/promotions/[a-zA-Z0-9-_]+", xml_text
                )
                for promo_url in set(early_urls):
                    if promo_url not in seen:
                        name = promo_url.split("/")[-1].replace("-", " ").title()
                        send_discord_card(
                            title=name,
                            url=promo_url,
                            keys_text="⏳ Early Sitemap Link / Check Keys",
                            source="Sitemap XML (Early Detection)",
                            ping=not first_v4_run,
                        )
                        seen.add(promo_url)
                        updated = True
                        cards_sent += 1
        except Exception as e:
            print(f"Sitemap scan error: {e}")

        # ---------------------------------------------------------
        # 2. LIVE PROMOTIONS PAGE: Click every sidebar tab & read Keys + Image
        # ---------------------------------------------------------
        try:
            print("Opening https://www.amdgaming.com/promotions ...")
            page.goto(
                "https://www.amdgaming.com/promotions",
                wait_until="networkidle",
                timeout=45000,
            )
            page.wait_for_timeout(5000)

            # Find all giveaway tabs on the left sidebar (or anywhere on the page)
            # We look for clickable elements containing "Giveaway", "Key", or "Bonus"
            tab_candidates = page.locator(
                "li, button, a, [role='tab'], div"
            ).filter(has_text=re.compile(r"Giveaway|Key\s+Giveaway", re.I))

            # Extract unique tab names from the left menu
            raw_texts = tab_candidates.all_inner_texts()
            tab_names = []
            for t in raw_texts:
                clean = t.strip()
                # Filter for clean single-line tab titles (like "World of Warships Naval Supplies Giveaway")
                if (
                    5 < len(clean) < 85
                    and "\n" not in clean
                    and "discover a trove" not in clean.lower()
                    and "featured giveaway" not in clean.lower()
                    and clean not in tab_names
                ):
                    tab_names.append(clean)

            print(f"Found promotion tabs: {tab_names}")

            # If no multiple tabs found, still scan the active featured giveaway
            if not tab_names:
                tab_names = ["Current Featured Promotion"]

            for tab_title in tab_names:
                try:
                    if tab_title != "Current Featured Promotion":
                        # Click the tab on the left sidebar to load its image and key count
                        tab_el = page.get_by_text(tab_title, exact=True).first
                        if tab_el.is_visible():
                            tab_el.click()
                            page.wait_for_timeout(2000)

                    body_text = page.inner_text("body")
                    html = page.content()

                    # Extract exact "XXXX Keys Remaining"
                    key_match = re.search(
                        r"([\d,]+\s+Keys?\s+Remaining)", body_text, re.I
                    )
                    keys_status = (
                        f"🟢 {key_match.group(1)}"
                        if key_match
                        else "🟢 Keys Available (Check Page)"
                    )
                    if key_match and key_match.group(1).strip().startswith("0 "):
                        keys_status = "🔴 0 Keys Remaining (Out of Stock)"

                    # Grab the main promo artwork image currently displayed
                    promo_img = None
                    imgs = page.locator("img").all()
                    for img in imgs:
                        src = img.get_attribute("src") or ""
                        width = img.bounding_box()
                        if (
                            src.startswith("http")
                            and "logo" not in src.lower()
                            and "avatar" not in src.lower()
                            and width
                            and width.get("width", 0) > 200
                        ):
                            promo_img = src
                            break

                    # Build the direct promotion link
                    promo_slug = slugify(tab_title)
                    direct_url = f"https://www.amdgaming.com/promotions/{promo_slug}"
                    track_id = f"PROMO_TAB::{promo_slug}"

                    if track_id not in seen or first_v4_run or is_manual:
                        send_discord_card(
                            title=tab_title,
                            url="https://www.amdgaming.com/promotions",
                            keys_text=keys_status,
                            image_url=promo_img,
                            source="AMD Promotions Page",
                            ping=(track_id not in seen and not first_v4_run),
                        )
                        seen.add(track_id)
                        seen.add(direct_url)
                        updated = True
                        cards_sent += 1

                except Exception as tab_err:
                    print(f"Error inspecting tab '{tab_title}': {tab_err}")

        except Exception as e:
            print(f"Promotions page scan error: {e}")

        browser.close()

    if first_v4_run:
        seen.add(INIT_FLAG)
        updated = True

    if updated:
        save_seen(seen)


if __name__ == "__main__":
    check_amd_promotions()
