import os
import time
import logging
import re
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# ---------------------------------
# CONFIGURATION
# ---------------------------------
CONFIG = {
    "CLIPS_DIR": "Videos",
    "UPLOADED_DIR": "Videos/Uploaded",
    "CLIENT_SECRETS_FILE": "client_secrets.json",
    "TOKEN_FILE": "token.json",
    "SCOPES": ["https://www.googleapis.com/auth/youtube.upload"],
    "PRIVACY_STATUS": "public",
    "CATEGORY_ID": "22",
    "TAGS": ["Shorts"],
    "MAX_RETRIES": 10
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------------------------
# AUTH
# ---------------------------------
def get_authenticated_service():
    creds = None
    if os.path.exists(CONFIG["TOKEN_FILE"]):
        creds = Credentials.from_authorized_user_file(CONFIG["TOKEN_FILE"], CONFIG["SCOPES"])

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CONFIG["CLIENT_SECRETS_FILE"],
                CONFIG["SCOPES"]
            )
            creds = flow.run_local_server(port=0)

        with open(CONFIG["TOKEN_FILE"], "w") as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)

# ---------------------------------
# HELPERS
# ---------------------------------
def parse_txt_file(txt_path: Path):
    lines = [line.strip() for line in txt_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return txt_path.stem, ""
    return lines[0], "\n".join(lines[1:]).strip()

def clean_title(title: str) -> str:
    cleaned = re.sub(r'[^\w\s\-\.,!?&@#]+', '', title)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

# ---------------------------------
# UPLOAD
# ---------------------------------
def upload_video(youtube, title, description, video_path):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": CONFIG["TAGS"],
            "categoryId": CONFIG["CATEGORY_ID"]
        },
        "status": {
            "privacyStatus": CONFIG["PRIVACY_STATUS"]
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    retry = 0
    while retry <= CONFIG["MAX_RETRIES"]:
        try:
            logging.info(f"Uploading video: {title}")
            status, response = request.next_chunk()
            if response and "id" in response:
                logging.info(f"Upload complete! Video ID: {response['id']}")
                return True
        except HttpError as e:
            logging.warning(f"HTTP error during upload: {e}")
            retry += 1
            time.sleep(min(60, 2 ** retry))

    logging.error("Failed to upload after max retries.")
    return False

# ---------------------------------
# MAIN — UPLOAD ONE VIDEO ONLY
# ---------------------------------
def uploader_once():
    youtube = get_authenticated_service()
    clips_root = Path(CONFIG["CLIPS_DIR"])

    # ====== LOAD UPLOAD HISTORY ======
    log_file = Path("uploaded_videos.txt")
    uploaded = set()

    if log_file.exists():
        uploaded = set(log_file.read_text().splitlines())

    folders = sorted([
        f for f in clips_root.iterdir()
        if f.is_dir()
        and f.name not in uploaded
        and not f.name.endswith("_skipped")
        and f.name != "Uploaded"
    ])

    if not folders:
        logging.info("No new clip folders left. Exiting.")
        return

    folder = folders[0]

    mp4_files = list(folder.glob("*.mp4"))
    txt_files = list(folder.glob("*.txt"))

    if not mp4_files:
        logging.warning(f"No video found in {folder}. Marking folder as skipped.")
        with open("uploaded_videos.txt", "a") as f:
            f.write(folder.name + "\n")
        return

    video_path = mp4_files[0]

    if txt_files:
        title, description = parse_txt_file(txt_files[0])
        title = clean_title(title)
    else:
        title = clean_title(video_path.stem)
        description = ""

    success = upload_video(youtube, title, description, str(video_path))

    if success:
        # ====== THIS IS THE REAL MEMORY ======
        with open("uploaded_videos.txt", "a") as f:
            f.write(folder.name + "\n")

        logging.info(f"Marked as uploaded: {folder.name}")

# ---------------------------------
if __name__ == "__main__":
    logging.info("Starting YouTube Shorts uploader...")
    uploader_once()
