import os
import json
import re
import time
import datetime
import xml.etree.ElementTree as ET
import requests

PROMOTIONS_PAGE_URL = "https://www.amdgaming.com/promotions"
SITEMAP_URL = "https://www.amdgaming.com/sitemap.xml"
SEEN_URLS_FILE = "seen_urls.json"

WEBHOOK_URL = "https://discord.com/api/webhooks/1563618599346770040/bZC29aczTJtkqsqZYt1i-9BbeAjjlGzIkMaKAamodSq0MKS4EDuJsexNeypCjJfo7VQA"
ROLE_ID = "1563847704541405324"

# Your live Ngrok forwarding endpoint for your local bot
LOCAL_BOT_ENDPOINT = "https://waving-penny-crudely.ngrok-free.dev/trigger-drop"

KEYWORDS = ["promotion", "promotions", "key", "claim", "reward", "giveaway", "redeem", "drop"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
        json.dump(list(seen_urls), f, indent=2)

def fetch_sitemap_urls():
    try:
        response = requests.get(SITEMAP_URL, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return []
        root = ET.fromstring(response.content)
        namespaces = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        urls = []
        for loc in root.findall('.//ns:loc', namespaces):
            if loc.text:
                urls.append(loc.text.strip())
        if not urls:
            for loc in root.iter():
                if loc.tag.endswith('loc') and loc.text:
                    urls.append(loc.text.strip())
        return urls
    except Exception as e:
        print(f"Sitemap check error: {e}")
        return []

def fetch_live_page_links():
    """Scrapes the live HTML directly to detect promotions before sitemap indexing."""
    try:
        response = requests.get(PROMOTIONS_PAGE_URL, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return []
        
        found_links = re.findall(r'href=[\'"]?([^\'" >]+)', response.text)
        cleaned_urls = []
        for link in found_links:
            if link.startswith("/"):
                cleaned_urls.append(f"https://www.amdgaming.com{link}")
            elif link.startswith("https://"):
                cleaned_urls.append(link)
        return list(set(cleaned_urls))
    except Exception as e:
        print(f"Page scrape error: {e}")
        return []

def send_discord_alert(url):
    payload = {
        "content": f"<@&{ROLE_ID}> 🚨 **NEW AMD GAME KEY DROP DETECTED!**",
        "embeds": [
            {
                "title": "🎁 Drop Link Found",
                "description": f"A brand new drop link has appeared live!\n\n👉 **[Click Here to Claim Key]({url})**",
                "color": 16711680,
                "fields": [
                    {
                        "name": "Target URL",
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
            print(f"⚠️ [LOCAL TRIGGER] Bot rejected with status: {response.status_code}")
    except Exception as e:
        print(f"❌ [LOCAL TRIGGER] Failed to reach local bot: {e}")

def check_for_drops():
    """Performs one pass comparing scraped and sitemap URLs against seen URLs."""
    seen_urls = load_seen_urls()
    collected_urls = set(fetch_sitemap_urls() + fetch_live_page_links())
    
    if not collected_urls:
        print("No URLs retrieved during check.")
        return False

    # Seed baseline if run with an empty file
    if not seen_urls:
        print("First run: Seeding baseline URLs into seen_urls.json...")
        save_seen_urls(collected_urls)
        return False

    new_drops_found = False
    for url in collected_urls:
        url_lower = url.lower()
        if url not in seen_urls and any(keyword in url_lower for keyword in KEYWORDS):
            if url_lower.rstrip("/") in ["https://www.amdgaming.com", "https://www.amdgaming.com/promotions"]:
                continue
            
            print(f"🔥 NEW DROP DETECTED: {url}")
            send_discord_alert(url)
            notify_local_bot(url)
            seen_urls.add(url)
            new_drops_found = True

    if new_drops_found:
        save_seen_urls(seen_urls)
        return True
    else:
        print("No new promotion/key drops found in this pass.")
        return False

def main():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    # Critical drop window: 15:50 UTC to 16:15 UTC (9:20 PM to 9:45 PM IST)
    in_critical_window = (now_utc.hour == 15 and now_utc.minute >= 50) or (now_utc.hour == 16 and now_utc.minute <= 15)

    if in_critical_window:
        print("🎯 ENTERING HIGH-SPEED SURVEILLANCE MODE (Polling every 10s)...")
        # Run loop for up to 15 minutes (900 seconds)
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
