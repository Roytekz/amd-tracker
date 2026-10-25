import json
import os
import re
import time
from playwright.sync_api import sync_playwright
import requests

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")
SEEN_FILE = "amd_seen.json"
INIT_FLAG = "PROMO_KEYS_V6_ARTWORK"


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


def send_discord_card(
    title,
    url,
    keys_text,
    image_url=None,
    screenshot_bytes=None,
    source="Promotions Page",
    ping=False,
):
    if not WEBHOOK_URL:
        print("CRITICAL ERROR: DISCORD_WEBHOOK secret is empty or missing!")
        return

    is_empty = "0 keys" in keys_text.lower()
    color = 9807270 if is_empty else 15548997

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

    payload = {
        "username": "AMD Key Drop Tracker",
        "embeds": [embed],
    }
    if ping and not is_empty:
        payload["content"] = (
            f"@everyone 🚨 **NEW AMD GAME KEY DROP: {title} ({keys_text})**"
        )

    try:
        # Priority 1: Direct high-res game artwork URL (if valid and not an AMD logo)
        if image_url and str(image_url).startswith("http"):
            embed["image"] = {"url": image_url}
            r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        # Priority 2: Direct cropped screenshot of the promotion banner
        elif screenshot_bytes:
            embed["image"] = {"url": "attachment://promo_artwork.png"}
            files = {
                "file": ("promo_artwork.png", screenshot_bytes, "image/png")
            }
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


def extract_accurate_artwork(page):
    """Finds the actual game promo banner on screen, skipping the top AMD Ryzen/Radeon logo."""
    return page.evaluate(
        """() => {
        const badWords = ['logo', 'ryzen', 'radeon', 'avatar', 'icon', 'header', 'footer', 'brand', 'nav', '.svg'];
        const isBadUrl = (u) => {
            if (!u || !u.startsWith('http')) return true;
            const lower = u.toLowerCase();
            return badWords.some(w => lower.includes(w));
        };

        let candidates = [];

        // 1. Check all visible <img> elements below the top navigation bar
        for (const img of document.querySelectorAll('img')) {
            const rect = img.getBoundingClientRect();
            const src = img.currentSrc || img.src || '';
            if (rect.top > 180 && rect.width >= 240 && rect.height >= 120 && !isBadUrl(src)) {
                candidates.push({ url: src, area: rect.width * rect.height });
            }
        }

        // 2. Check all elements with CSS background-image in the showcase area
        for (const el of document.querySelectorAll('*')) {
            const rect = el.getBoundingClientRect();
            if (rect.top > 180 && rect.width >= 240 && rect.height >= 120) {
                const bg = window.getComputedStyle(el).backgroundImage;
                if (bg && bg.includes('url(')) {
                    const match = bg.match(/url\\(["']?(https?:\\/\\/[^"')]+)["']?\\)/);
                    if (match && !isBadUrl(match[1])) {
                        candidates.push({ url: match[1], area: rect.width * rect.height });
                    }
                }
            }
        }

        // Pick the largest artwork banner in the main content area
        candidates.sort((a, b) => b.area - a.area);
        return candidates.length > 0 ? candidates[0].url : null;
    }"""
    )


def check_amd_promotions():
    seen = load_seen()
    is_manual = EVENT_NAME == "workflow_dispatch"
    first_v6_run = INIT_FLAG not in seen
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
                        send_discord_card(
                            title=name,
                            url=promo_url,
                            keys_text="⏳ Early Sitemap Link",
                            source="Sitemap XML (Early Detection)",
                            ping=not first_v6_run,
                        )
                        seen.add(promo_url)
                        updated = True
                        cards_sent += 1
        except Exception as e:
            print(f"Sitemap Error: {e}")

        # ---------------------------------------------------------
        # 2. Check Live Promotions Page Tabs, Keys & Game Artwork
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

            # Remove duplicate titles where one just has an exclamation mark on the right panel
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
                    # Click the tab in the left sidebar
                    page.evaluate(
                        """(targetText) => {
                            const els = Array.from(document.querySelectorAll('*'));
                            const match = els.find(el => el.children.length === 0 && el.textContent.trim() === targetText);
                            if (match) match.click();
                        }""",
                        tab_name,
                    )
                    page.wait_for_timeout(2500)  # Let the new game image and key count load

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

                    # Grab the accurate game artwork URL
                    img_url = extract_accurate_artwork(page)

                    # Fallback: If no clean URL found, take a cropped screenshot of the showcase area
                    shot_bytes = None
                    if not img_url:
                        shot_bytes = page.screenshot(
                            clip={"x": 420, "y": 380, "width": 1080, "height": 520}
                        )

                    slug = slugify(tab_name)
                    track_key = f"TAB::{slug}"

                    if track_key not in seen or first_v6_run or is_manual:
                        send_discord_card(
                            title=tab_name,
                            url="https://www.amdgaming.com/promotions",
                            keys_text=keys_str,
                            image_url=img_url,
                            screenshot_bytes=shot_bytes,
                            source="AMD Promotions Live Page",
                            ping=(track_key not in seen and not first_v6_run),
                        )
                        seen.add(track_key)
                        updated = True
                        cards_sent += 1
                except Exception as tab_e:
                    print(f"Tab error for {tab_name}: {tab_e}")

        except Exception as e:
            print(f"Promotions scan error: {e}")

        browser.close()

    if first_v6_run and cards_sent > 0:
        seen.add(INIT_FLAG)
        updated = True

    if updated:
        save_seen(seen)


if __name__ == "__main__":
    check_amd_promotions()
