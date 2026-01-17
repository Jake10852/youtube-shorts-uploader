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
    "MAX_RETRIES": 10,
    "UPLOAD_INTERVAL_HOURS": 5
}

# ---------------------------------
# LOGGING
# ---------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------------------------
# AUTHENTICATION
# ---------------------------------
def get_authenticated_service():
    creds = None
    if os.path.exists(CONFIG["TOKEN_FILE"]):
        creds = Credentials.from_authorized_user_file(CONFIG["TOKEN_FILE"], CONFIG["SCOPES"])

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CONFIG["CLIENT_SECRETS_FILE"], CONFIG["SCOPES"])
            creds = flow.run_local_server(port=0)
        with open(CONFIG["TOKEN_FILE"], "w") as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)

# ---------------------------------
# PARSE TITLE & DESCRIPTION
# ---------------------------------
def parse_txt_file(txt_path: Path):
    lines = [line.strip() for line in txt_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return txt_path.stem, ""
    title = lines[0]
    description = "\n".join(lines[1:]).strip()
    return title, description

# ---------------------------------
# CLEAN TITLE
# ---------------------------------
def clean_title(title: str) -> str:
    """Remove emojis and invalid characters from a YouTube video title."""
    cleaned = re.sub(r'[^\w\s\-\.,!?&@#]+', '', title)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

# ---------------------------------
# UPLOAD VIDEO
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
            sleep_time = min(60, 2 ** retry)
            logging.info(f"Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)

    logging.error("Failed to upload after max retries.")
    return False

# ---------------------------------
# UPLOADER ONCE
# ---------------------------------
def uploader_once():
    youtube = get_authenticated_service()

    clips_root = Path(CONFIG["CLIPS_DIR"])
    uploaded_root = Path(CONFIG["UPLOADED_DIR"])
    uploaded_root.mkdir(parents=True, exist_ok=True)  # make sure folder exists

    # Only folders that do NOT have 'uploaded' in their name
    folders = sorted([
        f for f in clips_root.iterdir()
        if f.is_dir() and "uploaded" not in f.name.lower()
    ])

    if not folders:
        logging.info("No new folders to upload. Exiting.")
        return

    folder = folders[0]  # pick the first folder
    mp4_files = list(folder.glob("*.mp4"))
    txt_files = list(folder.glob("*.txt"))

    if not mp4_files:
        logging.warning(f"No video found in {folder}. Marking folder as skipped.")
        new_name = folder.name
        if not new_name.lower().endswith("_skipped"):
            new_name += "_skipped"
        folder.rename(clips_root / new_name)
        return

    video_path = mp4_files[0]
    if txt_files:
        title, description = parse_txt_file(txt_files[0])
        title = clean_title(title)
    else:
        title = clean_title(video_path.stem)
        description = ""
        logging.warning(f"No .txt found for {folder.name}. Using filename as title.")

    success = upload_video(youtube, title, description, str(video_path))

    if success:
        # Mark folder as uploaded by renaming it
        new_name = folder.name + "_uploaded"
        folder.rename(clips_root / new_name)
        logging.info(f"Uploaded and marked folder: {new_name}")

# ---------------------------------
# ENTRY POINT
# ---------------------------------
if __name__ == "__main__":
    logging.info("Starting YouTube Shorts uploader...")
    uploader_once()
