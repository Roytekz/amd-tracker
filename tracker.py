import os
import json
import re
import time
import datetime
import requests

PROMOTIONS_URL = "https://www.amdgaming.com/promotions"
SEEN_URLS_FILE = "seen_urls.json"

WEBHOOK_URL = "https://discord.com/api/webhooks/1563618599346770040/bZC29aczTJtkqsqZYt1i-9BbeAjjlGzIkMaKAamodSq0MKS4EDuJsexNeypCjJfo7VQA"
ROLE_ID = "1563847704541405324"

# Live Ngrok endpoint
LOCAL_BOT_ENDPOINT = "https://waving-penny-crudely.ngrok-free.dev/trigger-drop"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.amdgaming.com/promotions"
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

def clean_image_url(image_url):
    """Ensures image URL has full protocol and valid format."""
    if not image_url:
        return None
    if image_url.startswith("//"):
        return f"https:{image_url}"
    elif image_url.startswith("/"):
        return f"https://www.amdgaming.com{image_url}"
    return image_url

def fetch_promotions():
    """Fetches promotion drop items along with title, target URL, and artwork."""
    found_items = []
    try:
        res = requests.get(PROMOTIONS_URL, headers=HEADERS, timeout=10)
        
        # Scenario A: Discourse responds with JSON
        if "application/json" in res.headers.get("Content-Type", ""):
            data = res.json()
            
            # 1. Standard Discourse topic objects
            topics = data.get("topic_list", {}).get("topics", [])
            for t in topics:
                slug = t.get("slug")
                topic_id = t.get("id")
                title = t.get("title", "AMD Promotion Drop")
                image = clean_image_url(t.get("image_url") or t.get("featured_link"))
                
                if slug and topic_id:
                    found_items.append({
                        "url": f"https://www.amdgaming.com/t/{slug}/{topic_id}",
                        "title": title,
                        "image": image
                    })
            
            # 2. Custom promotions plugin structure
            promos = data.get("promotions", []) or data.get("data", []) or data.get("items", [])
            for p in promos:
                slug = p.get("slug") or p.get("id")
                title = p.get("title") or p.get("name", "AMD Game Key")
                raw_image = (
                    p.get("image_url") or 
                    p.get("banner") or 
                    p.get("thumbnail") or 
                    p.get("cover")
                )
                image = clean_image_url(raw_image)
                link = p.get("url") or (f"https://www.amdgaming.com/promotions/{slug}" if slug else None)
                
                if link:
                    full_link = link if link.startswith("http") else f"https://www.amdgaming.com{link}"
                    found_items.append({
                        "url": full_link,
                        "title": title,
                        "image": image
                    })
                    
        # Scenario B: Fallback HTML parsing
        else:
            links = re.findall(r'href=[\'"]([^\'"]*promotions[^\'"]*|\/t\/[^\'"]+)[\'"]', res.text, re.IGNORECASE)
            for link in set(links):
                clean = link.split('#')[0].split('?')[0].strip()
                if clean in ["/promotions", "https://www.amdgaming.com/promotions"]:
                    continue
                full_url = f"https://www.amdgaming.com{clean}" if clean.startswith("/") else clean
                found_items.append({
                    "url": full_url,
                    "title": "AMD Game Promotion",
                    "image": None
                })

    except Exception as e:
        print(f"Error fetching promotions endpoint: {e}")

    return found_items

def send_discord_alert(url, title, image_url=None):
    embed_data = {
        "title": f"🎁 {title}",
        "description": f"A brand new game key drop is live!\n\n👉 **[Click Here to Claim Key]({url})**",
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

    # Embed artwork banner if present
    if image_url:
        embed_data["image"] = {"url": image_url}

    payload = {
        "content": f"<@&{ROLE_ID}> 🚨 **NEW AMD GAME KEY DROP DETECTED!**",
        "embeds": [embed_data]
    }

    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"Successfully alerted Discord for: {url} (Image: {image_url})")
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
    promotions = fetch_promotions()
    
    if not promotions:
        print("No promotions detected on this pass.")
        return False

    if not seen_urls:
        print("Seeding current promotions into seen_urls.json...")
        all_current = {p["url"] for p in promotions}
        save_seen_urls(all_current)
        return False

    new_drops_found = False
    for item in promotions:
        url = item["url"]
        title = item["title"]
        image = item.get("image")
        
        if url not in seen_urls:
            print(f"🔥 NEW GAME DROP DETECTED: {title} ({url})")
            send_discord_alert(url, title, image)
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
        while time.time() - start_time < 900:
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
