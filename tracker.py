import os
import json
import re
import xml.etree.ElementTree as ET
import requests

PROMOTIONS_PAGE_URL = "https://www.amdgaming.com/promotions"
SITEMAP_URL = "https://www.amdgaming.com/sitemap.xml"
SEEN_URLS_FILE = "seen_urls.json"

WEBHOOK_URL = "https://discord.com/api/webhooks/1563618599346770040/bZC29aczTJtkqsqZYt1i-9BbeAjjlGzIkMaKAamodSq0MKS4EDuJsexNeypCjJfo7VQA"
ROLE_ID = "1563847704541405324"

# Update this if your Ngrok domain changes
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
        
        # Extract all href links from the page
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
                "description": f"A new drop link was detected live!\n\n👉 **[Click Here to Claim Key]({url})**",
                "color": 16711680,
                "fields": [
                    {
                        "name": "Target",
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
            print(f"🚀 [LOCAL TRIGGER] Successfully triggered AMD bot for: {url}")
        else:
            print(f"⚠️ [LOCAL TRIGGER] Bot rejected status: {response.status_code}")
    except Exception as e:
        print(f"❌ [LOCAL TRIGGER] Failed to reach local bot: {e}")

def main():
    seen_urls = load_seen_urls()
    
    # Check both backend sitemap and live HTML page
    collected_urls = set(fetch_sitemap_urls() + fetch_live_page_links())
    
    if not collected_urls:
        print("No URLs retrieved.")
        return

    # Seed baseline if running on an empty file so it does not blast old links
    if not seen_urls:
        print("Seeding initial URL list into seen_urls.json...")
        save_seen_urls(collected_urls)
        return

    new_drops_found = False
    for url in collected_urls:
        url_lower = url.lower()
        # Matches any AMD drop URL containing our target keywords
        if url not in seen_urls and any(keyword in url_lower for keyword in KEYWORDS):
            # Ignore standard root pages
            if url_lower.rstrip("/") in ["https://www.amdgaming.com", "https://www.amdgaming.com/promotions"]:
                continue
            
            print(f"🔥 NEW DROP DISCOVERED: {url}")
            send_discord_alert(url)
            notify_local_bot(url)
            seen_urls.add(url)
            new_drops_found = True

    if new_drops_found:
        save_seen_urls(seen_urls)
    else:
        print("No new promotion/key drops found in this check.")

if __name__ == "__main__":
    main()
