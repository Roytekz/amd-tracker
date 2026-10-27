import os
import json
import time
import datetime
import requests

# Live API endpoints for the promotions category
PROMOTIONS_API_URLS = [
    "https://www.amdgaming.com/c/promotions.json",
    "https://www.amdgaming.com/promotions.json",
    "https://www.amdgaming.com/latest.json"
]

SEEN_URLS_FILE = "seen_urls.json"

WEBHOOK_URL = "https://discord.com/api/webhooks/1563618599346770040/bZC29aczTJtkqsqZYt1i-9BbeAjjlGzIkMaKAamodSq0MKS4EDuJsexNeypCjJfo7VQA"
ROLE_ID = "1563847704541405324"

# Set this to your live Ngrok endpoint
LOCAL_BOT_ENDPOINT = "https://waving-penny-crudely.ngrok-free.dev/trigger-drop"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

def load_seen_urls():
    if os.path.exists(SEEN_URLS_FILE):
        with open(SEEN_URLS_FILE, "r") as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                return set()
    return set()

def save_seen_urls(seen_urls):
    with open(SEEN_URLS_FILE, "w") as f:
        json.dump(sorted(list(seen_urls)), f, indent=2)

def fetch_live_promotion_topics():
    """Queries AMD Gaming's Discourse JSON API to get real, active promotion topics."""
    found_topics = []
    
    for api_url in PROMOTIONS_API_URLS:
        try:
            res = requests.get(api_url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                data = res.json()
                # Extract topics list from Discourse response
                topic_list = data.get("topic_list", {}).get("topics", [])
                for topic in topic_list:
                    slug = topic.get("slug")
                    topic_id = topic.get("id")
                    title = topic.get("title", "AMD Game Drop")
                    
                    if slug and topic_id:
                        # Real forum promotion link format
                        topic_url = f"https://www.amdgaming.com/t/{slug}/{topic_id}"
                        found_topics.append({
                            "url": topic_url,
                            "title": title
                        })
        except Exception as e:
            print(f"Error fetching API from {api_url}: {e}")
            continue

    return found_topics

def send_discord_alert(url, title):
    payload = {
        "content": f"<@&{ROLE_ID}> 🚨 **NEW AMD GAME KEY DROP DETECTED!**",
        "embeds": [
            {
                "title": f"🎁 {title}",
                "description": f"A brand new promotion topic went live!\n\n👉 **[Click Here to Claim Key]({url})**",
                "color": 16711680,
                "fields": [
                    {
                        "name": "Drop URL",
                        "value": f"`{url}`",
                        "inline": False
                    }
                ],
                "footer": {
                    "text": "AMD Drop Hunter • Real-time Web Tracker"
                }
            }
        ]
    }
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"Successfully alerted Discord for: {url}")
    except Exception as e:
        print(f"Error sending Discord webhook: {e}")

def notify_local_bot(url):
    payload = {"url": url}
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(LOCAL_BOT_ENDPOINT, json=payload, headers=headers, timeout=5)
        if response.status_code == 200:
            print(f"🚀 [LOCAL TRIGGER] Successfully fired AMD bot profiles for: {url}")
        else:
            print(f"⚠️ [LOCAL TRIGGER] Bot rejected status: {response.status_code}")
    except Exception as e:
        print(f"❌ [LOCAL TRIGGER] Failed to reach local bot: {e}")

def check_for_drops():
    seen_urls = load_seen_urls()
    topics = fetch_live_promotion_topics()
    
    if not topics:
        print("No topics found or unable to reach API.")
        return False

    # Seed baseline if running on empty seen_urls.json
    if not seen_urls:
        print("Seeding current active promotions into seen_urls.json...")
        all_current = {t["url"] for t in topics}
        save_seen_urls(all_current)
        return False

    new_drops_found = False
    for item in topics:
        url = item["url"]
        title = item["title"]
        
        if url not in seen_urls:
            print(f"🔥 NEW GAME DROP DETECTED: {title} ({url})")
            send_discord_alert(url, title)
            notify_local_bot(url)
            seen_urls.add(url)
            new_drops_found = True

    if new_drops_found:
        save_seen_urls(seen_urls)
        return True
    else:
        print("No new promotions found in this check.")
        return False

def main():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    # Critical drop window: 15:50 UTC to 16:15 UTC (9:20 PM to 9:45 PM IST)
    in_critical_window = (now_utc.hour == 15 and now_utc.minute >= 50) or (now_utc.hour == 16 and now_utc.minute <= 15)

    if in_critical_window:
        print("🎯 ENTERING HIGH-SPEED SURVEILLANCE MODE (Polling every 10s)...")
        start_time = time.time()
        while time.time() - start_time < 900:  # Run loop for 15 minutes
            found = check_for_drops()
            if found:
                print("Drop triggered and processed. Exiting surveillance loop.")
                break
            time.sleep(10)
    else:
        print("Standard single scheduled pass.")
        check_for_drops()

if __name__ == "__main__":
    main()
