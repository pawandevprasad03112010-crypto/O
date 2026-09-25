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

# Cloudinary Configuration
cloudinary.config(
    cloud_name="pfmjg7ip",
    api_key="368463435529631",
    api_secret="6u7lnfIRo4ikkXSR_GM2ziUtStM"
)

# AWS & DynamoDB Configuration
AWS_ACCESS_KEY_ID = "AKIA32VVAONMU6L6OLU3"
AWS_SECRET_ACCESS_KEY = "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"
REGION = "ap-south-1"
BUCKET_NAME = "property-images-estatex-1"
TABLE_NAME = "BUY_PROPERTY"
PRIMARY_KEY = "property_id"

DEFAULT_URL = "https://property-images-estatex-1.s3.ap-south-1.amazonaws.com/photo_1790242568046_001.png"
SKIP_LAST_N_IMAGES = 0  # सभी इमेजेस लेने के लिए इसे 0 किया गया है

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

migration_progress = {
    "status": "Idle",
    "logs": []
}

def add_log(msg: str):
    migration_progress["logs"].append(msg)
    if len(migration_progress["logs"]) > 150:
        migration_progress["logs"].pop(0)

def run_migration_task():
    global migration_progress
    migration_progress["status"] = "Running"
    migration_progress["logs"] = []
    
    try:
        add_log("🔄 क्लाउडिनेरी से सभी इमेजेस फेच की जा रही हैं...")
        
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
        
        if SKIP_LAST_N_IMAGES > 0 and total_fetched > SKIP_LAST_N_IMAGES:
            resources_to_upload = resources[:-SKIP_LAST_N_IMAGES]
        else:
            resources_to_upload = resources

        add_log(f"📦 कुल {total_fetched} इमेजेस S3 पर अपलोड हो रही हैं...")
        
        s3_uploaded_urls = []
        for i, res in enumerate(resources_to_upload):
            img_url = res.get("secure_url")
            public_id = res.get("public_id")
            format_ext = res.get("format", "jpg")
            filename = f"{str(public_id).replace('/', '_')}.{format_ext}"
            
            success = False
            for attempt in range(3):
                try:
                    resp = requests.get(img_url, timeout=15)
                    if resp.status_code == 200:
                        img_data = io.BytesIO(resp.content)
                        s3_key = f"migrated_images/{filename}"
                        s3_client.upload_fileobj(img_data, BUCKET_NAME, s3_key, ExtraArgs={"ContentType": "image/*"})
                        new_s3_url = f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/{s3_key}"
                        s3_uploaded_urls.append(new_s3_url)
                        success = True
                        break
                except Exception:
                    import time
                    time.sleep(1)
            
            if success:
                if (i + 1) % 10 == 0 or (i + 1) == len(resources_to_upload):
                    add_log(f"✅ S3 Uploaded ({i+1}/{len(resources_to_upload)})")
            else:
                add_log(f"⚠️ स्किप किया गया (Fail): {filename}")

        add_log("🔍 DynamoDB से कोलकाता लोकेशन की लिस्टिंग्स जांची जा रही हैं...")
        response = table.scan()
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))

        kolkata_items = []
        for item in items:
            loc = item.get("location")
            if isinstance(loc, dict):
                city = str(loc.get("city", "")).lower()
                sub_loc = str(loc.get("sub_locality", "")).lower()
                if "kolkata" in city or "kolkata" in sub_loc:
                    kolkata_items.append(item)

        add_log(f"📍 कोलकाता की कुल {len(kolkata_items)} लिस्टिंग्स मिलीं। अपडेट जारी है...")

        url_index = 0
        updated_count = 0
        total_s3_urls = len(s3_uploaded_urls)

        for item in kolkata_items:
            property_id = item.get(PRIMARY_KEY)
            media = item.get("media", {})
            images = media.get("images", []) if isinstance(media, dict) else []

            if url_index >= total_s3_urls:
                add_log("⚠️ सभी नई S3 इमेजेस समाप्त हो चुकी हैं।")
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
            
            if not target_replaced:
                new_images.extend(new_batch)

            table.update_item(
                Key={PRIMARY_KEY: property_id},
                UpdateExpression="SET media.images = :new_images",
                ExpressionAttributeValues={":new_images": new_images}
            )
            updated_count += 1
            add_log(f"✨ Updated Property ID: {property_id} (इमेजेस जोड़ी गईं: {len(new_batch)})")

        migration_progress["status"] = "Completed"
        add_log(f"🎉 प्रक्रिया पूरी हो गई! कुल {updated_count} कोलकाता लिस्टिंग्स अपडेट कर दी गई हैं।")

    except Exception as e:
        migration_progress["status"] = "Error"
        add_log(f"❌ एरर आ गया: {str(e)}")

@app.get("/", response_class=HTMLResponse)
def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/start")
def start_migration(background_tasks: BackgroundTasks):
    if migration_progress["status"] == "Running":
        return {"message": "पहले से चल रहा है..."}
    background_tasks.add_task(run_migration_task)
    return {"message": "शुरू हो गया!"}

@app.get("/api/logs")
def get_logs():
    return migration_progress
            
