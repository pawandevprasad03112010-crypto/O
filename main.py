import json
import cloudscraper
from bs4 import BeautifulSoup
from flask import Flask, render_template, request

app = Flask(__name__)

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

            try:
                scraper = cloudscraper.create_scraper(
                    browser={'browser': 'chrome', 'platform': 'android', 'desktop': False}
                )
                response = scraper.get(url, timeout=30)
                
                if response.status_code != 200:
                    error_msg = f"Server blocked request! Status code: {response.status_code}"
                else:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    script_tag = soup.find('script', id='__NEXT_DATA__')
                    
                    if script_tag:
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

                            listing_obj = {
                                "id": count,
                                "title": title,
                                "price": price,
                                "posted_by": posted_by,
                                "listing_type": form_data["listing_type"].upper(),
                                "phone_number": "Protected by 99acres (Requires Subscription)"
                            }
                            listings_data.append(listing_obj)
                            count += 1
                    else:
                        error_msg = "Could not locate JSON state (__NEXT_DATA__). Page structure might have changed."
            except Exception as e:
                error_msg = f"Connection error: {str(e)}"

    return render_template('index.html', listings=listings_data, form=form_data, error=error_msg)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
    
