import os
import time
import logging
import re
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2 import service_account

# ---------------------------------
# CONFIGURATION
# ---------------------------------
CONFIG = {
    "CLIPS_DIR": "Videos",
    "UPLOADED_DIR": r"C:\Users\PC\OneDrive\Documents\GitHub\automated_youtube_channel\Videos\Uploaded",

    # This will now be SERVICE ACCOUNT JSON
    "CLIENT_SECRETS_FILE": "service_account.json",

    "PRIVACY_STATUS": "public",
    "CATEGORY_ID": "22",
    "TAGS": ["Shorts"],
    "MAX_RETRIES": 10,
}


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------------------------
# AUTH
# ---------------------------------
def get_authenticated_service():
    """
    Authenticate using Google SERVICE ACCOUNT instead of OAuth user token.
    Works perfectly in GitHub Actions.
    """
    credentials = service_account.Credentials.from_service_account_file(
        CONFIG["CLIENT_SECRETS_FILE"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )

    return build("youtube", "v3", credentials=credentials)


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
