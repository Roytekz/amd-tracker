import os
import json
import xml.etree.ElementTree as ET
import requests

SITEMAP_URL = "https://www.amdgaming.com/sitemap.xml"
SEEN_URLS_FILE = "seen_urls.json"

WEBHOOK_URL = "https://discord.com/api/webhooks/1563618599346770040/bZC29aczTJtkqsqZYt1i-9BbeAjjlGzIkMaKAamodSq0MKS4EDuJsexNeypCjJfo7VQA"
ROLE_ID = "1563847704541405324"  # Your @AMD Drop Hunter role ID

# Your active Ngrok bridge endpoint pointing to your local AMD bot
LOCAL_BOT_ENDPOINT = "https://waving-penny-crudely.ngrok-free.dev/trigger-drop"

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

    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code == 204:
            print(f"Successfully alerted Discord for: {url}")
        else:
            print(f"Failed to send Discord webhook: {response.status_code}, {response.text}")
    except Exception as e:
        print(f"Error sending Discord webhook: {e}")

def notify_local_bot(url):
    """Sends the drop URL directly to your running AMD proxy bot via Ngrok."""
    payload = {"url": url}
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(LOCAL_BOT_ENDPOINT, json=payload, headers=headers, timeout=5)
        if response.status_code == 200:
            print(f"🚀 [LOCAL TRIGGER] Successfully fired AMD bot profiles for: {url}")
        else:
            print(f"⚠️ [LOCAL TRIGGER] Bot rejected request with status: {response.status_code}")
    except Exception as e:
        print(f"❌ [LOCAL TRIGGER] Failed to reach local bot via Ngrok: {e}")

def main():
    seen_urls = load_seen_urls()
    current_urls = fetch_sitemap_urls()
    
    if not current_urls:
        print("No URLs retrieved from sitemap.")
        return

    new_drops_found = False
    for url in current_urls:
        # Strict filter: Must contain 'promotion' or 'key', and must not have been seen before
        if url not in seen_urls and ("promotion" in url.lower() or "key" in url.lower()):
            print(f"New drop discovered: {url}")
            
            # 1. Dispatch Discord ping
            send_discord_alert(url)
            
            # 2. Trigger local AMD 20-account bot instantly
            notify_local_bot(url)
            
            seen_urls.add(url)
            new_drops_found = True

    if new_drops_found:
        save_seen_urls(seen_urls)
    else:
        print("No new promotion/key drops found in this check.")

if __name__ == "__main__":
    main()
