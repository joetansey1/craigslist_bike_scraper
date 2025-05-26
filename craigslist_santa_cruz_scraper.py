import re
import time
import json
import requests
from bs4 import BeautifulSoup
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium import webdriver

# CONFIG
SEARCH_URL = "https://sfbay.craigslist.org/search/bia?query=santa+cruz&sort=date"
CHROMEDRIVER_BIN = "/usr/bin/chromedriver"
CHROME_BIN = "/usr/bin/chromium-browser"
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1375853092301443154/PWDXxIkTNZGJ6QdiBo4zvUWNEItgSmwYn1Do7Q0ShzwH15SpUySnvOVW_67y4ZvhAj4v"
MODEL_KEYWORDS = ["bronson", "5010", "nomad"]
SIZE_KEYWORDS = [
    "large", "size l", "frame size l", "l frame", "l ",
    "lrg", "lrg.", "lg", "lg.", "size large", "frame size: large"
]
YEAR_REGEX = re.compile(r"\b(20\d{2})\b")


def post_to_discord(listing):
    def clean_fields(fields):
        seen = set()
        cleaned = []
        for field in fields:
            name = field.get("name")
            value = field.get("value")
            if not name or value is None:
                continue
            value_str = str(value).strip()
            if not value_str or name in seen:
                continue
            seen.add(name)
            cleaned.append({
                "name": name,
                "value": value_str,
                "inline": field.get("inline", True)
            })
        return cleaned

    base_fields = [
        {"name": "Price", "value": f"${listing.get('price')}" if listing.get('price') else "N/A", "inline": True},
        {"name": "Model", "value": listing.get("model", "Unknown"), "inline": True},
        {"name": "Size", "value": listing.get("size", "Unknown"), "inline": True},
        {"name": "Year", "value": listing.get("year", "N/A"), "inline": True},
        {"name": "Location", "value": listing.get("location", "Unknown"), "inline": True}
    ]

    redundant_fields = []
    if listing.get("price"):
        redundant_fields.append({"name": "Price", "value": f"${listing['price']}", "inline": True})
    if listing.get("model"):
        redundant_fields.append({"name": "Model", "value": listing['model'], "inline": True})
    if listing.get("size"):
        redundant_fields.append({"name": "Size", "value": listing['size'], "inline": True})
    if listing.get("year"):
        redundant_fields.append({"name": "Year", "value": listing['year'], "inline": True})

    all_fields = clean_fields(base_fields + redundant_fields)

    embed = {
        "title": listing.get('title') or "Untitled Listing",
        "url": listing.get('url'),
        "fields": all_fields
    }

    if listing.get("thumb"):
        embed["thumbnail"] = {"url": listing["thumb"]}

    if not all_fields:
        embed["description"] = "⚠️ Partial listing – no metadata fields extracted."

    data = {"embeds": [embed]}

    print("📤 Sending to Discord:")
    print(json.dumps(data, indent=2))

    response = requests.post(DISCORD_WEBHOOK_URL, json=data)
    if response.status_code != 204:
        print(f"❌ Failed to post to Discord: {response.status_code}, {response.text}")
    else:
        print("✅ Posted to Discord")
def extract_listings(driver, urls):
    listings = []

    for idx, url in enumerate(urls[:200]):
        print(f"🔎 [{idx}] Scraping: {url}")
        try:
            driver.get(url)

            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "attrgroup"))
                )
            except Exception as wait_err:
                print(f"⚠️ Timeout waiting for .attrgroup on {url}: {wait_err}")
                continue

            detail_soup = BeautifulSoup(driver.page_source, "html.parser")

            title = detail_soup.find("span", id="titletextonly")
            title_text = title.get_text(strip=True).lower() if title else ""

            # Parse the attribute summary
            attrgroup = detail_soup.find("div", class_="attrgroup")
            print(f"📦 Rawdiv. attrgroup HTML:\n{attrgroup.encode('utf-8') if attrgroup else 'None'}\n")
            attr_kv = {}

            if attrgroup:
                print("🧵 .get_text():", attrgroup.get_text(separator="|"))
                print("🧵 .decode_contents():", attrgroup.decode_contents())
                sub_attrs = attrgroup.find_all("div", class_="attr")
                for div in sub_attrs:
                    label = div.find("span", class_="labl")
                    value = div.find("span", class_="valu")
                    if label and value:
                        key = label.get_text(strip=True).lower().rstrip(":")
                        val = value.get_text(strip=True).lower()
                        attr_kv[key] = val

            # Combine for matching
            summary_blob = f"{title_text} {' '.join(attr_kv.values())}"
            model = next((m for m in MODEL_KEYWORDS if m in summary_blob), None)

            # Extract size
            size = None
            frame_size = attr_kv.get("frame size")
            if frame_size:
                frame_size_clean = frame_size.strip().lower()
                if frame_size_clean in SIZE_KEYWORDS:
                    size = frame_size_clean
                else:
                    print(f"❌ Unrecognized frame size: '{frame_size_clean}'")
            else:
                size = next((s for s in SIZE_KEYWORDS if s in summary_blob), None)
                if size:
                    print(f"🕵️ Fallback matched size: {size}")

            # Extract year
            body_elem = detail_soup.find("section", id="postingbody")
            raw_body = body_elem.get_text(separator="\n", strip=True).lower() if body_elem else ""
            split_body = re.split(r"(?:\n\s*\n){2,}|keywords:", raw_body)
            clean_body = split_body[0] if split_body else raw_body
            year_match = YEAR_REGEX.search(f"{title_text} {clean_body}")
            year = year_match.group(1) if year_match else None

            # Other fields
            price = detail_soup.find("span", class_="price")
            price_val = price.get_text(strip=True).replace("$", "") if price else "N/A"

            thumb = detail_soup.find("img")
            thumb_url = thumb["src"] if thumb and thumb.get("src", "").startswith("http") else None

            location_tag = detail_soup.find("div", class_="mapaddress")
            location = location_tag.get_text(strip=True) if location_tag else "Unknown"
            if location == "Unknown":
                alt = detail_soup.find("small")
                if alt:
                    location = alt.get_text(strip=True)

            print(f"🧪 model: {model}, size: {size}, attrs: {attr_kv}")

            if model and size:
                listing = {
                    "title": title_text,
                    "model": model,
                    "size": size,
                    "year": year,
                    "price": price_val,
                    "thumb": thumb_url,
                    "location": location,
                    "url": url,
                }
                listings.append(listing)
                post_to_discord(listing)

        except Exception as e:
            print(f"⚠️ Skipping {url} due to error: {e}")
            continue

    return listings

def main():
    chrome_options = Options()
    chrome_options.binary_location = CHROME_BIN
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    service = Service(CHROMEDRIVER_BIN)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(15)

    print(f"🌐 Loading: {SEARCH_URL}")
    driver.get(SEARCH_URL)
    time.sleep(3)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    anchors = soup.select("a.cl-app-anchor.cl-search-anchor[href]")
    urls = []

    for a in anchors:
        href = a.get("href")
        if href and href.startswith("/"):
            href = "https://sfbay.craigslist.org" + href
        if href and href.startswith("http") and "/search/" not in href:
            urls.append(href)

    print(f"🔍 Found {len(urls)} gallery URLs")

    listings = extract_listings(driver, urls)
    driver.quit()

    print(f"\n🎯 Matched Listings ({len(listings)}):")
    for idx, l in enumerate(listings):
        print(f"\n[{idx}] {l['title']}")
        print(f"📌 URL: {l['url']}")
        print(f"💰 Price: ${l['price']}")
        print(f"🛠 Model: {l['model']}, Size: {l['size']}, Year: {l.get('year')}")
        print(f"🖼 Thumb: {l.get('thumb')}")


if __name__ == "__main__":
    main()
