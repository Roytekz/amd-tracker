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
INIT_FLAG = "PROMO_KEYS_V10_EARLY_CATCH"

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


def capture_amd_showcase_artwork(page):
    """Captures the exact banner image displayed inside AMD's giveaway showcase area."""
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
            f"👉 **[CLICK HERE TO CLAIM]({url})**\n\n"
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
        # 1. EARLY DETECTION: Monitor Forum JSON API for Hidden/Early Drop Announcements
        # ---------------------------------------------------------
        try:
            print("Checking AMD Forum API for early drops...")
            resp = page.goto(
                "https://www.amdgaming.com/latest.json", timeout=20000
            )
            if resp and resp.status == 200:
                data = json.loads(page.inner_text("body"))
                topics = data.get("topic_list", {}).get("topics", [])
                for topic in topics:
                    title = topic.get("title", "")
                    slug = topic.get("slug", "")
                    topic_id = topic.get("id", "")
                    img_url = topic.get("image_url")
                    closed = topic.get("closed", False)

                    # Look for giveaway, key, drop, sweepstakes keywords
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

                        if track_key not in seen:
                            send_discord_card(
                                title=title,
                                url=topic_url,
                                keys_text=status,
                                image_url=img_url,
                                source="AMD Forum API (Early Detection)",
                                ping=(not first_run and not closed),
                            )
                            seen.add(track_key)
                            updated = True
                            cards_sent += 1
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

            print(f"Detected Giveaway Tabs: {cleaned_tabs}")

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

                    amd_img_url, amd_shot_bytes = capture_amd_showcase_artwork(
                        page
                    )

                    slug = slugify(tab_name)
                    track_key = f"TAB::{slug}"

                    if track_key not in seen or first_run or is_manual:
                        send_discord_card(
                            title=tab_name,
                            url="https://www.amdgaming.com/promotions",
                            keys_text=keys_str,
                            image_url=amd_img_url,
                            screenshot_bytes=amd_shot_bytes,
                            source="AMD Promotions Live Page",
                            ping=(track_key not in seen and not first_run),
                        )
                        seen.add(track_key)
                        updated = True
                        cards_sent += 1
                except Exception as tab_e:
                    print(f"Tab error for {tab_name}: {tab_e}")

        except Exception as e:
            print(f"Promotions scan error: {e}")

        browser.close()

    if first_run and cards_sent > 0:
        seen.add(INIT_FLAG)
        updated = True

    if updated:
        save_seen(seen)


if __name__ == "__main__":
    check_amd_promotions()
