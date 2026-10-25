import json
import os
import re
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


def notify_discord(title, url, source):
    if not WEBHOOK_URL:
        return
    payload = {
        "username": "AMD Early Monitor",
        "content": "@everyone",
        "embeds": [
            {
                "title": f"🚨 {title}",
                "url": url,
                "description": f"Detected via **{source}**",
                "color": 15548997,
            }
        ],
    }
    requests.post(WEBHOOK_URL, json=payload)


def check_amd():
    seen = load_seen()
    is_first_run = len(seen) == 0
    updated = False

    urls_to_check = [
        ("Sitemap", "https://www.amdgaming.com/sitemap.xml"),
        ("Promotions Page", "https://www.amdgaming.com/promotions"),
    ]

    for source_name, target_url in urls_to_check:
        try:
            resp = scraper.get(target_url, timeout=15)
            if resp.status_code == 200:
                matches = re.findall(
                    r"(?:https://www\.amdgaming\.com)?(/promotions/[a-zA-Z0-9-_]+)",
                    resp.text,
                )
                for path in set(matches):
                    full_url = f"https://www.amdgaming.com{path}"
                    if full_url not in seen:
                        slug_name = (
                            path.split("/")[-1].replace("-", " ").title()
                        )
                        if not is_first_run:
                            notify_discord(
                                f"New Promotion: {slug_name}",
                                full_url,
                                source_name,
                            )
                        seen.add(full_url)
                        updated = True
        except Exception as e:
            print(f"[{source_name}] Check failed: {e}")

    try:
        resp = scraper.get("https://www.amdgaming.com/latest.json", timeout=15)
        if resp.status_code == 200:
            topics = resp.json().get("topic_list", {}).get("topics", [])
            for topic in topics:
                title = topic.get("title", "")
                slug = topic.get("slug", "")
                topic_id = topic.get("id", "")
                if any(
                    k in title.lower()
                    for k in [
                        "giveaway",
                        "sweepstakes",
                        "free",
                        "drop",
                        "key",
                        "promotion",
                    ]
                ):
                    topic_url = f"https://www.amdgaming.com/t/{slug}/{topic_id}"
                    if topic_url not in seen:
                        if not is_first_run:
                            notify_discord(title, topic_url, "Forum JSON API")
                        seen.add(topic_url)
                        updated = True
    except Exception as e:
        print(f"[JSON API] Check failed: {e}")

    if is_first_run and updated:
        notify_discord(
            "Bot Activated & Working!",
            "https://www.amdgaming.com/promotions",
            "First Setup Complete",
        )

    if updated:
        save_seen(seen)


if __name__ == "__main__":
    check_amd()
