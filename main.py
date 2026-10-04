import json
import cloudscraper
from bs4 import BeautifulSoup
from flask import Flask, render_template, request

app = Flask(__name__)

def scrape_with_cloudscraper(url):
    try:
        scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'android', 'desktop': False}
        )
        response = scraper.get(url, timeout=25)
        if response.status_code == 200:
            return response.text
    except Exception:
        pass
    return None

def scrape_with_playwright(url):
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()
            page.goto(url, timeout=40000)
            page.wait_for_timeout(3000) # Wait for dynamic JS to load
            html_content = page.content()
            browser.close()
            return html_content
    except Exception as e:
        print(f"Playwright error: {e}")
    return None

@app.route('/', methods=['GET', 'POST'])
def index():
    listings_data = []
    error_msg = ""
    
    form_data = {
        "location": "",
        "listing_type": "buy",
        "prop_type": "residential",
        "bhk": "all"
    }

    if request.method == 'POST':
        form_data["location"] = request.form.get('location', '').strip()
        form_data["listing_type"] = request.form.get('listing_type', 'buy')
        form_data["prop_type"] = request.form.get('prop_type', 'residential')
        form_data["bhk"] = request.form.get('bhk', 'all')

        if form_data["location"]:
            loc_slug = form_data["location"].lower().replace(" ", "-")
            base_url = "https://www.99acres.com/"
            
            if form_data["listing_type"] == "pg":
                url = f"{base_url}pg-in-{loc_slug}-ffid"
            else:
                bhk_part = f"{form_data['bhk']}-bhk-" if form_data['bhk'] != "all" else ""
                url = f"{base_url}{bhk_part}property-in-{loc_slug}-ffid?preference={form_data['listing_type']}"

            # Step 1: Try Cloudscraper first (Fast)
            html_text = scrape_with_cloudscraper(url)
            
            # Step 2: Fallback to Playwright if Cloudscraper fails or gets blocked
            if not html_text:
                print("Cloudscraper blocked/failed. Switching to Playwright engine...")
                html_text = scrape_with_playwright(url)

            if html_text:
                soup = BeautifulSoup(html_text, 'html.parser')
                script_tag = soup.find('script', id='__NEXT_DATA__')
                
                if script_tag:
                    try:
                        json_data = json.loads(script_tag.string)
                        props = json_data.get('props', {}).get('pageProps', {})
                        results = props.get('searchResult', {}).get('propertyResults', [])
                        
                        if not results:
                            results = props.get('initialData', {}).get('propertyResults', [])

                        count = 1
                        for item in results:
                            title = item.get('heading', item.get('propertyTitle', 'N/A'))
                            price = item.get('priceLabel', item.get('price', 'N/A'))
                            posted_by = item.get('postedBy', item.get('dealerName', 'Broker / Agent'))
                            user_type = str(item.get('userType', '')).lower()
                            
                            # Filter: Keep only Brokers, skip Owners
                            if 'owner' in user_type or 'owner' in str(posted_by).lower():
                                continue
                            
                            prop_nature = str(item.get('propertyType', '')).lower()
                            if form_data["prop_type"] == "commercial" and "residential" in prop_nature:
                                continue
                            elif form_data["prop_type"] == "residential" and "commercial" in prop_nature:
                                continue

                            listing_obj = {
                                "id": count,
                                "title": title,
                                "price": price,
                                "posted_by": posted_by,
                                "listing_type": form_data["listing_type"].upper(),
                                "category": form_data["prop_type"].capitalize(),
                                "phone_number": "Protected by 99acres (Requires Subscription)"
                            }
                            listings_data.append(listing_obj)
                            count += 1
                    except Exception as parse_e:
                        error_msg = f"Data parsing error: {str(parse_e)}"
                else:
                    error_msg = "Could not locate JSON state (__NEXT_DATA__). Anti-bot wall might be active."
            else:
                error_msg = "Both engines (Cloudscraper & Playwright) failed to fetch data from 99acres."

    return render_template('index.html', listings=listings_data, form=form_data, error=error_msg)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
    
