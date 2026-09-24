import io
import asyncio
import boto3
import cloudinary
import cloudinary.api
import cloudinary.uploader
import requests
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- 1. आपकी नई क्लाउडिनेरी कॉन्फ़िगरेशन ---
cloudinary.config(
    cloud_name="pfmjg7ip",
    api_key="368463435529631",
    api_secret="6u7lnfIRo4ikkXSR_GM2ziUtStM"
)

# --- 2. AWS & DynamoDB कॉन्फ़िगरेशन ---
AWS_ACCESS_KEY_ID = "AKIA32VVAONMU6L6OLU3"
AWS_SECRET_ACCESS_KEY = "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"
REGION = "ap-south-1"
BUCKET_NAME = "property-images-estatex-1"
TABLE_NAME = "BUY_PROPERTY"
PRIMARY_KEY = "property_id"

DEFAULT_URL = "https://property-images-estatex-1.s3.ap-south-1.amazonaws.com/photo_1790242568046_001.png"
SKIP_LAST_N_IMAGES = 1200

s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=REGION
)

dynamodb = boto3.resource(
    "dynamodb",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=REGION
)
table = dynamodb.Table(TABLE_NAME)

# माइग्रेशन स्टेटस ट्रैक करने के लिए
migration_status = {"status": "Idle", "message": "स्टार्ट बटन दबाने की प्रतीक्षा है..."}

def run_migration_task():
    global migration_status
    try:
        migration_status["status"] = "Running"
        migration_status["message"] = "क्लाउडिनेरी से इमेजेस फेच की जा रही हैं..."
        
        resources = []
        next_cursor = None
        while True:
            options = {"max_results": 500}
            if next_cursor:
                options["next_cursor"] = next_cursor
            result = cloudinary.api.resources(**options)
            resources.extend(result.get("resources", []))
            next_cursor = result.get("next_cursor")
            if not next_cursor:
                break

        resources.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        total_fetched = len(resources)
        
        if total_fetched > SKIP_LAST_N_IMAGES:
            resources_to_upload = resources[:-SKIP_LAST_N_IMAGES]
        else:
            resources_to_upload = []

        migration_status["message"] = f"कुल {total_fetched} में से आखिरी {SKIP_LAST_N_IMAGES} छोड़कर {len(resources_to_upload)} इमेजेस S3 पर अपलोड हो रही हैं..."
        
        s3_uploaded_urls = []
        for res in resources_to_upload:
            img_url = res["secure_url"]
            public_id = res["public_id"]
            format_ext = res["format"]
            filename = f"{public_id.replace('/', '_')}.{format_ext}"
            try:
                resp = requests.get(img_url, timeout=10)
                if resp.status_code == 200:
                    img_data = io.BytesIO(resp.content)
                    s3_key = f"migrated_images/{filename}"
                    s3_client.upload_fileobj(img_data, BUCKET_NAME, s3_key, ExtraArgs={"ContentType": "image/*"})
                    new_s3_url = f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/{s3_key}"
                    s3_uploaded_urls.append(new_s3_url)
            except Exception:
                continue

        migration_status["message"] = "DynamoDB से कोलकाता लिस्टिंग्स जांची जा रही हैं..."
        response = table.scan()
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))

        kolkata_items = []
        for item in items:
            loc = item.get("location", {})
            city = str(loc.get("city", "")).lower()
            sub_loc = str(loc.get("sub_locality", "")).lower()
            if "kolkata" in city or "kolkata" in sub_loc:
                kolkata_items.append(item)

        url_index = 0
        updated_count = 0
        total_s3_urls = len(s3_uploaded_urls)

        for item in kolkata_items:
            property_id = item.get(PRIMARY_KEY)
            media = item.get("media", {})
            images = media.get("images", [])

            default_url_count = images.count(DEFAULT_URL)
            if default_url_count < 2:
                continue

            if url_index >= total_s3_urls:
                break

            batch_size = 5
            new_batch = s3_uploaded_urls[url_index:url_index + batch_size]
            url_index += len(new_batch)

            new_images = []
            target_replaced = False
            for img in images:
                if img == DEFAULT_URL and not target_replaced:
                    new_images.extend(new_batch)
                    target_replaced = True
                elif img == DEFAULT_URL and target_replaced:
                    continue
                else:
                    new_images.append(img)

            table.update_item(
                Key={PRIMARY_KEY: property_id},
                UpdateExpression="SET media.images = :new_images",
                ExpressionAttributeValues={":new_images": new_images}
            )
            updated_count += 1

        migration_status["status"] = "Completed"
        migration_status["message"] = f"प्रक्रिया सफल! कुल {updated_count} कोलकाता लिस्टिंग्स अपडेट कर दी गई हैं।"

    except Exception as e:
        migration_status["status"] = "Error"
        migration_status["message"] = f"त्रुटि आई: {str(e)}"

@app.get("/", response_class=HTMLResponse)
def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/start-migration")
def start_migration(background_tasks: BackgroundTasks):
    if migration_status["status"] == "Running":
        return {"status": "Already Running", "message": "माइग्रेशन पहले से चल रहा है..."}
    background_tasks.add_task(run_migration_task)
    return {"status": "Started", "message": "माइग्रेशन बैकग्राउंड में शुरू हो चुका है!"}

@app.get("/api/status")
def get_status():
    return migration_status

