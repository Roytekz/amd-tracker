import os
import json
import xml.etree.ElementTree as ET
import requests

SITEMAP_URL = "https://www.amdgaming.com/sitemap.xml"
SEEN_URLS_FILE = "seen_urls.json"

WEBHOOK_URL = "https://discord.com/api/webhooks/1563618599346770040/bZC29aczTJtkqsqZYt1i-9BbeAjjlGzIkMaKAamodSq0MKS4EDuJsexNeypCjJfo7VQA"
ROLE_ID = "1563847704541405324"

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
                    
        print(f"Successfully fetched {len(urls)} total URLs from sitemap.")
        return urls
    except Exception as e:
        print(f"Error fetching sitemap: {e}")
        return []

def send_discord_alert(url):
    payload = {
        "content": f"<@&{ROLE_ID}> 🚨 **NEW AMD GAME KEY DROP DETECTED!**",
        "embeds": [
            {
                "title": "🎁 Early Sitemap Drop Found",
                "description": f"A brand new drop link has just been indexed on the backend sitemap!\n\n👉 **[Click Here to Claim Key]({url})**",
                "color": 16711680,
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
        print(f"Successfully alerted Discord for: {url}")
    else:
        print(f"Failed to send webhook: {response.status_code}, {response.text}")

def main():
    seen_urls = load_seen_urls()
    current_urls = fetch_sitemap_urls()
    
    if not current_urls:
        print("No URLs retrieved from sitemap.")
        return

    new_drops_found = False
    match_count = 0

    for url in current_urls:
        # Check for promotion or key keywords
        if "promotion" in url.lower() or "key" in url.lower():
            match_count += 1
            if url not in seen_urls:
                print(f"New matching drop discovered: {url}")
                send_discord_alert(url)
                seen_urls.add(url)
                new_drops_found = True
            else:
                print(f"Already seen matching drop (skipped): {url}")

    print(f"Total matching promotion/key URLs found in sitemap: {match_count}")

    if new_drops_found:
        save_seen_urls(seen_urls)
    else:
        print("No new unseen drops found.")

if __name__ == "__main__":
    main()
