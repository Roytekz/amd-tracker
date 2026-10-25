import os
import json
import xml.etree.ElementTree as ET
import requests

SITEMAP_URL = "https://www.amdgaming.com/sitemap.xml"
SEEN_URLS_FILE = "seen_urls.json"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
ROLE_ID = "1563847704541405325"  # Your @AMD Drop Hunter role ID

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
        response = requests.get(SITEMAP_URL, timeout=10)
        response.raise_for_status()
        
        # Parse XML sitemap
        root = ET.fromstring(response.content)
        
        # Handle namespaces if present in sitemap format
        namespaces = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        urls = []
        
        # Find all <loc> tags
        for loc in root.findall('.//ns:loc', namespaces):
            if loc.text:
                urls.append(loc.text.strip())
                
        # Fallback if namespace isn't strictly matched
        if not urls:
            for loc in root.iter():
                if loc.tag.endswith('loc') and loc.text:
                    urls.append(loc.text.strip())
                    
        return urls
    except Exception as e:
        print(f"Error fetching sitemap: {e}")
        return []

def send_discord_alert(url):
    if not WEBHOOK_URL:
        print("Webhook URL not found in environment variables.")
        return

    # Filter keywords to ensure it's a promotion/drop link
    if "promotion" not in url.lower() and "key" not in url.lower():
        return

    payload = {
        "content": f"<@&{ROLE_ID}> 🚨 **NEW AMD GAME KEY DROP DETECTED!**",
        "embeds": [
            {
                "title": "🎁 Early Sitemap Drop Found",
                "description": f"A brand new drop link has just been indexed on the backend sitemap!\n\n👉 **[Click Here to Claim Key]({url})**",
                "color": 16711680,  # Red alert color
                "fields": [
                    {
                        "name": "Status",
                        "value": "`⏳ Early Sitemap Link`",
                        "inline": True
                    }
                ],
                "footer": {
                    "text": "AMD Drop Hunter • Automated Early Tracker"
                }
            }
        ]
    }

    response = requests.post(WEBHOOK_URL, json=payload)
    if response.status_code == 204:
        print(f"Successfully alerted for: {url}")
    else:
        print(f"Failed to send webhook: {response.status_code}, {response.text}")

def main():
    seen_urls = load_seen_urls()
    current_urls = fetch_sitemap_urls()
    
    if not current_urls:
        print("No URLs retrieved from sitemap.")
        return

    new_drops_found = False
    for url in current_urls:
        # Check if it's a promotion link and hasn't been seen yet
        if url not in seen_urls and ("promotion" in url.lower() or "key" in url.lower()):
            print(f"New drop discovered: {url}")
            send_discord_alert(url)
            seen_urls.add(url)
            new_drops_found = True

    if new_drops_found:
        save_seen_urls(seen_urls)
    else:
        print("No new drops found in this check.")

if __name__ == "__main__":
    main()
