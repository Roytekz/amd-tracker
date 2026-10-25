import json
import os
import re
import time
from playwright.sync_api import sync_playwright
import requests

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")
SEEN_FILE = "amd_seen.json"
INIT_FLAG = "PROMO_KEYS_V5"


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


def send_discord_card(title, url, keys_text, image_url=None, source="Promotions Page", ping=False):
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

    if image_url and str(image_url).startswith("http"):
        embed["image"] = {"url": image_url}

    payload = {
        "username": "AMD Key Drop Tracker",
        "embeds": [embed],
    }
    if ping and not is_empty:
        payload["content"] = f"@everyone 🚨 **NEW AMD GAME KEY DROP: {title} ({keys_text})**"

    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"Discord Webhook Response for '{title}': HTTP {r.status_code}")
        time.sleep(1.5)
    except Exception as e:
        print(f"Webhook delivery failed: {e}")


def check_amd_promotions():
    seen = load_seen()
    is_manual = EVENT_NAME == "workflow_dispatch"
    first_v5_run = INIT_FLAG not in seen
    updated = False
    cards_sent = 0
    debug_log = []

    with sync_playwright() as p:
        # Use stealth flags so Cloudflare doesn't detect headless mode
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
        # Hide navigator.webdriver flag
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        # Intercept any background JSON responses containing promotion data
        network_blobs = []

        def handle_response(response):
            try:
                ctype = response.headers.get("content-type", "")
                if "json" in ctype or "promotions" in response.url:
                    txt = response.text()
                    if "giveaway" in txt.lower() or "keys" in txt.lower():
                        network_blobs.append(txt)
            except Exception:
                pass

        page.on("response", handle_response)

        # ---------------------------------------------------------
        # 1. Check Sitemap XML for Early Unlisted /promotions/ Links
        # ---------------------------------------------------------
        try:
            resp = page.goto(
                "https://www.amdgaming.com/sitemap.xml",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            status_code = resp.status if resp else "None"
            debug_log.append(f"Sitemap HTTP: {status_code}")
            if resp and resp.status == 200:
                xml_text = page.content()
                early_urls = re.findall(
                    r"https://www\.amdgaming\.com/promotions/[a-zA-Z0-9-_]+", xml_text
                )
                for promo_url in set(early_urls):
                    if promo_url not in seen or first_v5_run or is_manual:
                        name = promo_url.split("/")[-1].replace("-", " ").title()
                        send_discord_card(
                            title=name,
                            url=promo_url,
                            keys_text="🟢 Active / Early Sitemap Link",
                            source="Sitemap XML",
                            ping=(promo_url not in seen and not first_v5_run),
                        )
                        seen.add(promo_url)
                        updated = True
                        cards_sent += 1
        except Exception as e:
            debug_log.append(f"Sitemap Error: {str(e)[:60]}")

        # ---------------------------------------------------------
        # 2. Check Live Promotions Page (DOM + JS Tabs + Network)
        # ---------------------------------------------------------
        try:
            print("Opening https://www.amdgaming.com/promotions ...")
            resp = page.goto(
                "https://www.amdgaming.com/promotions",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(7000)  # Let JS render the left sidebar & key count

            page_title = page.title()
            body_text = page.inner_text("body")
            html = page.content()
            debug_log.append(
                f"Promos HTTP: {resp.status if resp else 'None'} | Title: {page_title}"
            )

            # Extract any /promotions/game-key-giveaway slugs from HTML or Network JSON
            combined_source = html + "\n" + "\n".join(network_blobs)
            slug_matches = re.findall(
                r"/promotions[/#]([a-zA-Z0-9-_]+giveaway[a-zA-Z0-9-_]*)",
                combined_source,
                re.I,
            )

            # Find all visible text lines on the page that end with "Giveaway" (Matches the left sidebar!)
            lines = [line.strip() for line in body_text.split("\n") if line.strip()]
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

            debug_log.append(f"Sidebar Tabs Found: {len(sidebar_tabs)} | Slugs: {len(set(slug_matches))}")
            print(f"Detected Sidebar Tabs: {sidebar_tabs}")

            # Click each sidebar tab and grab its image + "XXXX Keys Remaining"
            for tab_name in sidebar_tabs:
                try:
                    # Click via JS so overlays don't block Playwright
                    page.evaluate(
                        """(targetText) => {
                            const els = Array.from(document.querySelectorAll('*'));
                            const match = els.find(el => el.children.length === 0 && el.textContent.trim() === targetText);
                            if (match) match.click();
                        }""",
                        tab_name,
                    )
                    page.wait_for_timeout(2000)

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

                    # Grab the visible game artwork image via JS
                    img_url = page.evaluate(
                        """() => {
                            const imgs = Array.from(document.querySelectorAll('img'));
                            const bigImg = imgs.find(i => i.width > 220 && i.height > 100 && i.src.startsWith('http') && !i.src.toLowerCase().includes('logo'));
                            return bigImg ? bigImg.src : null;
                        }"""
                    )

                    slug = slugify(tab_name)
                    track_key = f"TAB::{slug}"
                    promo_link = f"https://www.amdgaming.com/promotions/{slug}"

                    if track_key not in seen or first_v5_run or is_manual:
                        send_discord_card(
                            title=tab_name,
                            url="https://www.amdgaming.com/promotions",
                            keys_text=keys_str,
                            image_url=img_url,
                            source="AMD Promotions Live Page",
                            ping=(track_key not in seen and not first_v5_run),
                        )
                        seen.add(track_key)
                        seen.add(promo_link)
                        updated = True
                        cards_sent += 1
                except Exception as tab_e:
                    print(f"Tab error for {tab_name}: {tab_e}")

            # Also report any hidden slugs caught in the background network/HTML
            for s in set(slug_matches):
                track_key = f"TAB::{s.lower()}"
                full_link = f"https://www.amdgaming.com/promotions/{s}"
                if track_key not in seen and full_link not in seen:
                    clean_title = s.replace("-", " ").title()
                    send_discord_card(
                        title=clean_title,
                        url=full_link,
                        keys_text="⏳ Early / Hidden Promotion Link",
                        source="Background Code Scanner",
                        ping=not first_v5_run,
                    )
                    seen.add(track_key)
                    seen.add(full_link)
                    updated = True
                    cards_sent += 1

        except Exception as e:
            debug_log.append(f"Promos Error: {str(e)[:80]}")

        browser.close()

    if first_v5_run and cards_sent > 0:
        seen.add(INIT_FLAG)
        updated = True

    if updated:
        save_seen(seen)

    # ---------------------------------------------------------
    # 3. GUARANTEED DIAGNOSTIC MESSAGE IF 0 CARDS WERE SENT
    # ---------------------------------------------------------
    if cards_sent == 0 and (is_manual or first_v5_run):
        diag_text = "\n".join([f"• `{line}`" for line in debug_log])
        send_discord_card(
            title="Tracker Diagnostic Report",
            url="https://www.amdgaming.com/promotions",
            keys_text="See Diagnostic Log Below",
            source=f"GitHub Actions\n\n**What the server saw:**\n{diag_text}",
            ping=False,
        )


if __name__ == "__main__":
    check_amd_promotions()
