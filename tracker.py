import json
import os
import re
import time
import cloudscraper
import requests

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
SEEN_FILE = "amd_seen.json"
VISUAL_CARDS_FLAG = "VISUAL_CARDS_SENT_V2"

scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_set):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(list(seen_set)), f, indent=2)


def inspect_promo_page(url, main_page_html=""):
    """Scrapes the promotion page for the Game Banner Image and Key/Giveaway Status."""
    image_url = None
    status = "🟢 Active / Keys Available (Check Link)"

    try:
        target_html = main_page_html
        if not target_html or "/promotions#" not in url:
            resp = scraper.get(url, timeout=12)
            if resp.status_code == 200:
                target_html = resp.text

        if target_html:
            # 1. Grab Official Banner Image (OpenGraph or Twitter meta image)
            img_match = re.search(
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                target_html,
                re.I,
            )
            if not img_match:
                img_match = re.search(
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                    target_html,
                    re.I,
                )
            if not img_match:
                img_match = re.search(
                    r'<img[^>]+src=["\'](https://[^"\']+\.(?:jpg|png|webp))["\']',
                    target_html,
                    re.I,
                )

            if img_match:
                image_url = img_match.group(1)
                if image_url.startswith("/"):
                    image_url = f"https://www.amdgaming.com{image_url}"

            # 2. Check for Key Availability / Status keywords
            lower_html = target_html.lower()
            if any(
                x in lower_html
                for x in [
                    "all keys claimed",
                    "out of keys",
                    "no keys left",
                    "reward has ended",
                    "competition has ended",
                ]
            ):
                status = "🔴 Ended / Out of Keys"
            elif any(
                x in lower_html
                for x in ["coming soon", "starts in", "upcoming", "not started"]
            ):
                status = "⏳ Upcoming (Detected Early!)"
            elif "keys remaining" in lower_html or "keys left" in lower_html:
                # Try to extract exact number if publicly visible in HTML
                key_count = re.search(
                    r"(\d+[\d,]*)\s+keys?\s+(?:remaining|left)", lower_html
                )
                if key_count:
                    status = f"🟢 **{key_count.group(1)} Keys Remaining!**"
    except Exception as e:
        print(f"Inspect failed for {url}: {e}")

    return image_url, status


def notify_discord(title, url, source, image_url=None, status=None, ping=True):
    if not WEBHOOK_URL:
        return

    if not image_url or not status:
        fetched_img, fetched_status = inspect_promo_page(url)
        image_url = image_url or fetched_img
        status = status or fetched_status

    embed = {
        "title": f"🎮 {title}",
        "url": url,
        "description": (
            f"🔑 **Status:** {status}\n"
            f"👉 **[CLICK HERE TO CLAIM / VIEW]({url})**\n\n"
            f"📡 *Source: **{source}***"
        ),
        "color": 15548997 if "🔴" not in status else 9807270,
    }

    if image_url:
        embed["image"] = {"url": image_url}

    payload = {
        "username": "AMD Early Monitor",
        "embeds": [embed],
    }
    if ping:
        payload["content"] = "@everyone 🔥 **New AMD Drop Detected!**"

    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=10)
        time.sleep(1.5)  # Safe pause for Discord rate limits
    except Exception as e:
        print(f"Webhook delivery error: {e}")


def check_amd():
    seen = load_seen()
    is_first_run = len(seen) == 0
    send_visual_catchup = VISUAL_CARDS_FLAG not in seen
    updated = False

    urls_to_check = [
        ("Sitemap XML", "https://www.amdgaming.com/sitemap.xml"),
        ("Promotions Page", "https://www.amdgaming.com/promotions"),
    ]

    for source_name, target_url in urls_to_check:
        try:
            resp = scraper.get(target_url, timeout=15)
            if resp.status_code == 200:
                promo_matches = re.findall(
                    r"(?:https://www\.amdgaming\.com)?(/promotions[/#][a-zA-Z0-9-_]+)",
                    resp.text,
                )
                for path in set(promo_matches):
                    full_url = f"https://www.amdgaming.com{path}"
                    slug_name = (
                        re.split(r"[/#]", path)[-1].replace("-", " ").title()
                    )

                    if full_url not in seen or send_visual_catchup:
                        img, status = inspect_promo_page(full_url, resp.text)
                        notify_discord(
                            f"AMD Promotion: {slug_name}",
                            full_url,
                            source_name,
                            image_url=img,
                            status=status,
                            ping=(not is_first_run and not send_visual_catchup),
                        )
                        seen.add(full_url)
                        updated = True

                gleam_matches = re.findall(
                    r"https://gleam\.io/[a-zA-Z0-9]{5}/[a-zA-Z0-9-_]+",
                    resp.text,
                )
                for gleam_url in set(gleam_matches):
                    gleam_name = (
                        gleam_url.split("/")[-1].replace("-", " ").title()
                    )
                    if gleam_url not in seen or send_visual_catchup:
                        img, status = inspect_promo_page(gleam_url)
                        notify_discord(
                            f"Gleam Giveaway: {gleam_name}",
                            gleam_url,
                            f"{source_name} (Gleam)",
                            image_url=img,
                            status=status,
                            ping=(not is_first_run and not send_visual_catchup),
                        )
                        seen.add(gleam_url)
                        updated = True
        except Exception as e:
            print(f"[{source_name}] Check failed: {e}")

    json_endpoints = [
        ("Sweepstakes Category", "https://www.amdgaming.com/c/sweepstakes.json"),
        ("Latest Forum Feed", "https://www.amdgaming.com/latest.json"),
    ]

    keywords = [
        "giveaway",
        "sweepstakes",
        "free",
        "drop",
        "key",
        "promotion",
        "claim",
        "bundle",
    ]

    for api_name, api_url in json_endpoints:
        try:
            resp = scraper.get(api_url, timeout=15)
            if resp.status_code == 200:
                topics = resp.json().get("topic_list", {}).get("topics", [])
                for topic in topics:
                    title = topic.get("title", "")
                    slug = topic.get("slug", "")
                    topic_id = topic.get("id", "")
                    image_url = topic.get("image_url")
                    closed = topic.get("closed", False)

                    is_sweepstakes_feed = "sweepstakes.json" in api_url
                    has_keyword = any(k in title.lower() for k in keywords)

                    if is_sweepstakes_feed or has_keyword:
                        if "historical sweepstakes" in title.lower():
                            continue

                        topic_url = f"https://www.amdgaming.com/t/{slug}/{topic_id}"
                        status = (
                            "🔴 Ended / Thread Closed"
                            if closed
                            else "🟢 Active / Open for Entry"
                        )

                        if topic_url not in seen or (
                            send_visual_catchup and not closed
                        ):
                            notify_discord(
                                title,
                                topic_url,
                                api_name,
                                image_url=image_url,
                                status=status,
                                ping=(
                                    not is_first_run and not send_visual_catchup
                                ),
                            )
                            seen.add(topic_url)
                            updated = True
        except Exception as e:
            print(f"[{api_name}] Check failed: {e}")

    if send_visual_catchup:
        seen.add(VISUAL_CARDS_FLAG)
        updated = True

    if updated:
        save_seen(seen)


if __name__ == "__main__":
    check_amd()
