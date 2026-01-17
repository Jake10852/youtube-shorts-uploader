import os
import time
import logging
import re
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# ---------------------------------
# CONFIGURATION
# ---------------------------------
CONFIG = {
    "CLIPS_DIR": "Videos",
    "UPLOADED_DIR": "Videos/Uploaded",  # relative path for both local and GitHub Actions
    "CLIENT_SECRETS_FILE": "client_secrets.json",
    "TOKEN_FILE": "token.json",
    "SCOPES": ["https://www.googleapis.com/auth/youtube.upload"],
    "PRIVACY_STATUS": "public",
    "CATEGORY_ID": "22",
    "TAGS": ["Shorts"],
    "MAX_RETRIES": 5
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

    # Load existing token if available
    if os.path.exists(CONFIG["TOKEN_FILE"]):
        creds = Credentials.from_authorized_user_file(CONFIG["TOKEN_FILE"], CONFIG["SCOPES"])

    # Refresh or create credentials if needed
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logging.info("Refreshing expired token...")
            creds.refresh(Request())
        else:
            logging.info("Running OAuth flow for new credentials...")
            flow = InstalledAppFlow.from_client_secrets_file(CONFIG["CLIENT_SECRETS_FILE"], CONFIG["SCOPES"])
            creds = flow.run_local_server(port=0)
        with open(CONFIG["TOKEN_FILE"], "w") as token_file:
            token_file.write(creds.to_json())

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
    # Remove emojis & non-standard symbols
    cleaned = re.sub(r"[^\w\s\-\.,!?&@#]+", "", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
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
# UPLOAD ONE VIDEO
# ---------------------------------
def uploader_once():
    youtube = get_authenticated_service()
    clips_root = Path(CONFIG["CLIPS_DIR"])
    uploaded_root = Path(CONFIG["UPLOADED_DIR"])
    uploaded_root.mkdir(exist_ok=True)

    # Get all folders ready for upload
    folders = sorted([
        f for f in clips_root.iterdir()
        if f.is_dir() and f.name != "Uploaded" and not f.name.endswith("_skipped")
    ])

    if not folders:
        logging.info("No clip folders left. Exiting.")
        return

    folder = folders[0]  # pick the first folder
    mp4_files = list(folder.glob("*.mp4"))
    txt_files = list(folder.glob("*.txt"))

    if not mp4_files:
        logging.warning(f"No video found in {folder}. Marking folder as skipped.")
        new_name = folder.name
        if not new_name.endswith("_skipped"):
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
        dest = uploaded_root / folder.name
        folder.rename(dest)
        logging.info(f"Uploaded and moved folder: {folder.name}")

# ---------------------------------
# ENTRY POINT
# ---------------------------------
if __name__ == "__main__":
    logging.info("Starting YouTube Shorts uploader...")
    uploader_once()
