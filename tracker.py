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

def clean_image_url(url):
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if url.startswith("//"):
        return f"https:{url}"
    elif url.startswith("/"):
        return f"https://www.amdgaming.com{url}"
    elif url.startswith("http://") or url.startswith("https://"):
        return url
    return None

def fetch_artwork_from_page(target_url):
    """Scrapes og:image meta tag directly from the drop page if not in JSON list."""
    try:
        html_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        res = requests.get(target_url, headers=html_headers, timeout=5)
        if res.status_code == 200:
            # 1. Match OpenGraph image: <meta property="og:image" content="...">
            og_match = re.search(r'<meta\s+property=[\'"]og:image[\'"]\s+content=[\'"]([^\'"]+)[\'"]', res.text, re.IGNORECASE)
            if not og_match:
                og_match = re.search(r'<meta\s+content=[\'"]([^\'"]+)[\'"]\s+property=[\'"]og:image[\'"]', res.text, re.IGNORECASE)
            
            if og_match:
                return clean_image_url(og_match.group(1))

            # 2. Match Twitter image: <meta name="twitter:image" content="...">
            tw_match = re.search(r'<meta\s+(?:name|property)=[\'"]twitter:image[\'"]\s+content=[\'"]([^\'"]+)[\'"]', res.text, re.IGNORECASE)
            if tw_match:
                return clean_image_url(tw_match.group(1))
    except Exception as e:
        print(f"Failed to fetch page artwork for {target_url}: {e}")
    return None

def fetch_promotions():
    found_items = []
    try:
        res = requests.get(PROMOTIONS_URL, headers=HEADERS, timeout=10)
        
        if "application/json" in res.headers.get("Content-Type", ""):
            data = res.json()
            
            # Discourse topic objects
            topics = data.get("topic_list", {}).get("topics", [])
            for t in topics:
                slug = t.get("slug")
                topic_id = t.get("id")
                title = t.get("title", "AMD Promotion Drop")
                raw_img = t.get("image_url") or t.get("featured_link")
                
                if slug and topic_id:
                    found_items.append({
                        "url": f"https://www.amdgaming.com/t/{slug}/{topic_id}",
                        "title": title,
                        "image": clean_image_url(raw_img)
                    })
            
            # Custom promotions plugin list
            promos = data.get("promotions", []) or data.get("data", []) or data.get("items", [])
            for p in promos:
                slug = p.get("slug") or p.get("id")
                title = p.get("title") or p.get("name", "AMD Game Key")
                
                # Check all common image field names used by Discourse plugins
                raw_img = (
                    p.get("image_url") or
                    p.get("banner_url") or
                    p.get("banner") or
                    p.get("thumbnail") or
                    p.get("thumbnail_url") or
                    p.get("cover") or
                    p.get("cover_image") or
                    p.get("header_image") or
                    p.get("promo_image") or
                    p.get("image")
                )
                
                link = p.get("url") or (f"https://www.amdgaming.com/promotions/{slug}" if slug else None)
                if link:
                    full_link = link if link.startswith("http") else f"https://www.amdgaming.com{link}"
                    found_items.append({
                        "url": full_link,
                        "title": title,
                        "image": clean_image_url(raw_img)
                    })

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
        print(f"Error fetching promotions: {e}")

    return found_items

def send_discord_alert(url, title, image_url=None):
    # If the JSON list had no image, resolve it via the drop page's open-graph meta tag
    if not image_url:
        print(f"Image not in JSON feed, resolving artwork from {url}...")
        image_url = fetch_artwork_from_page(url)

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

    if image_url:
        embed_data["image"] = {"url": image_url}
        print(f"Attached Artwork Banner: {image_url}")

    payload = {
        "content": f"<@&{ROLE_ID}> 🚨 **NEW AMD GAME KEY DROP DETECTED!**",
        "embeds": [embed_data]
    }

    try:
        res = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        if res.status_code in [200, 204]:
            print(f"Successfully alerted Discord for: {url}")
        else:
            print(f"Discord API returned status {res.status_code}: {res.text}")
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
