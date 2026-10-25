import json
import os
import re
import time
import cloudscraper
import requests

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
SEEN_FILE = "amd_seen.json"

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


def notify_discord(title, url, source, image_url=None):
    if not WEBHOOK_URL:
        print("Error: DISCORD_WEBHOOK secret not found!")
        return

    embed = {
        "title": f"🚨 {title}",
        "url": url,
        "description": (
            f"👉 **[CLICK HERE TO OPEN PROMOTION]({url})**\n\n"
            f"📡 *Detected early via: **{source}***"
        ),
        "color": 15548997,  # AMD Red
    }

    if image_url:
        embed["image"] = {"url": image_url}

    payload = {
        "username": "AMD Early Monitor",
        "content": "@everyone 🔥 **New AMD Drop Detected!**",
        "embeds": [embed],
    }

    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=10)
        time.sleep(1)  # Prevents Discord rate-limiting if multiple links drop at once
    except Exception as e:
        print(f"Webhook delivery error: {e}")


def check_amd():
    seen = load_seen()
    is_first_run = len(seen) == 0
    updated = False

    # 1. Check Sitemap & Promotions Page for URLs, Anchor Tags (#), and Gleam Links
    urls_to_check = [
        ("Sitemap XML", "https://www.amdgaming.com/sitemap.xml"),
        ("Promotions Page", "https://www.amdgaming.com/promotions"),
    ]

    for source_name, target_url in urls_to_check:
        try:
            resp = scraper.get(target_url, timeout=15)
            if resp.status_code == 200:
                # UPGRADE 1: Catches both /promotions/name AND /promotions#name
                promo_matches = re.findall(
                    r"(?:https://www\.amdgaming\.com)?(/promotions[/#][a-zA-Z0-9-_]+)",
                    resp.text,
                )
                for path in set(promo_matches):
                    full_url = f"https://www.amdgaming.com{path}"
                    if full_url not in seen:
                        slug_name = (
                            re.split(r"[/#]", path)[-1]
                            .replace("-", " ")
                            .title()
                        )
                        if not is_first_run:
                            notify_discord(
                                f"New Promotion: {slug_name}",
                                full_url,
                                source_name,
                            )
                        seen.add(full_url)
                        updated = True

                # UPGRADE 2: Catches embedded Gleam.io giveaway links directly
                gleam_matches = re.findall(
                    r"https://gleam\.io/[a-zA-Z0-9]{5}/[a-zA-Z0-9-_]+",
                    resp.text,
                )
                for gleam_url in set(gleam_matches):
                    if gleam_url not in seen:
                        gleam_name = (
                            gleam_url.split("/")[-1].replace("-", " ").title()
                        )
                        if not is_first_run:
                            notify_discord(
                                f"Direct Gleam Giveaway: {gleam_name}",
                                gleam_url,
                                f"{source_name} (Gleam Embed)",
                            )
                        seen.add(gleam_url)
                        updated = True
        except Exception as e:
            print(f"[{source_name}] Check failed: {e}")

    # 2. UPGRADE 3: Check both Latest Forum JSON and Dedicated Sweepstakes Category JSON
    json_endpoints = [
        ("Sweepstakes Category API", "https://www.amdgaming.com/c/sweepstakes.json"),
        ("Latest Forum API", "https://www.amdgaming.com/latest.json"),
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
        "win",
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
                    image_url = topic.get("image_url")  # UPGRADE 4: Grabs promo banner image

                    # Every post in Sweepstakes Category OR matching keywords in Latest
                    is_sweepstakes_feed = "sweepstakes.json" in api_url
                    has_keyword = any(k in title.lower() for k in keywords)

                    if is_sweepstakes_feed or has_keyword:
                        # Skip pinned historical winner threads
                        if "historical sweepstakes" in title.lower():
                            continue

                        topic_url = f"https://www.amdgaming.com/t/{slug}/{topic_id}"
                        if topic_url not in seen:
                            if not is_first_run:
                                notify_discord(
                                    title, topic_url, api_name, image_url
                                )
                            seen.add(topic_url)
                            updated = True
        except Exception as e:
            print(f"[{api_name}] Check failed: {e}")

    if is_first_run and updated:
        notify_discord(
            "Upgraded Bot Activated & Working!",
            "https://www.amdgaming.com/promotions",
            "System Upgrade Complete",
        )

    if updated:
        save_seen(seen)


if __name__ == "__main__":
    check_amd()
