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
    "UPLOADED_FILE": "uploaded_videos.txt",  # File to track uploaded folders
    "UPLOADED_DIR": "Videos/Uploaded",
    "CLIENT_SECRETS_FILE": "client_secrets.json",
    "TOKEN_FILE": "token.json",
    "SCOPES": ["https://www.googleapis.com/auth/youtube.upload"],
    "PRIVACY_STATUS": "public",
    "CATEGORY_ID": "22",
    "TAGS": ["Shorts"],
    "MAX_RETRIES": 10
}

# ---------------------------------
# LOGGING
# ---------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------------------------
# WRITE SECRETS FROM ENV
# ---------------------------------
def write_secret(env_var: str, file_name: str):
    value = os.getenv(env_var)
    if not value:
        logging.error(f"Missing required secret: {env_var}")
        raise RuntimeError(f"Missing required secret: {env_var}")
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(value)
    logging.info(f"Wrote {file_name} from secret {env_var}")

# ---------------------------------
# AUTHENTICATION
# ---------------------------------
def get_authenticated_service():
    write_secret("GOOGLE_SERVICE_ACCOUNT_JSON", CONFIG["CLIENT_SECRETS_FILE"])
    write_secret("YOUTUBE_TOKEN_JSON", CONFIG["TOKEN_FILE"])

    creds = None
    if os.path.exists(CONFIG["TOKEN_FILE"]):
        creds = Credentials.from_authorized_user_file(CONFIG["TOKEN_FILE"], CONFIG["SCOPES"])

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
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

    for attempt in range(CONFIG["MAX_RETRIES"]):
        try:
            logging.info(f"Uploading video: {title}")
            _, response = request.next_chunk()
            if response and "id" in response:
                logging.info(f"Upload complete! Video ID: {response['id']}")
                return True
        except HttpError as e:
            logging.warning(f"HTTP error during upload: {e}")
            time.sleep(2 ** attempt)
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

    uploaded_file_path = Path(CONFIG["UPLOADED_FILE"])
    if not uploaded_file_path.exists():
        uploaded_file_path.touch()

    # Load uploaded folders
    with uploaded_file_path.open("r", encoding="utf-8") as f:
        uploaded_folders = set(line.strip() for line in f.readlines())

    # Find next folder to upload
    folders = sorted([
        f for f in clips_root.iterdir()
        if f.is_dir() and f.name not in uploaded_folders
    ])

    if not folders:
        logging.info("No new clip folders left. Exiting.")
        return

    folder = folders[0]
    mp4_files = list(folder.glob("*.mp4"))
    txt_files = list(folder.glob("*.txt"))

    if not mp4_files:
        logging.warning(f"No video found in {folder}. Skipping folder.")
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
        # Track uploaded folder
        with uploaded_file_path.open("a", encoding="utf-8") as f:
            f.write(f"{folder.name}\n")
        logging.info(f"Uploaded folder recorded in {CONFIG['UPLOADED_FILE']}: {folder.name}")

# ---------------------------------
# ENTRY POINT
# ---------------------------------
if __name__ == "__main__":
    logging.info("Starting YouTube Shorts uploader (single-run)...")
    uploader_once()
