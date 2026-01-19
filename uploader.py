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
import subprocess
import json

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
    "MAX_RETRIES": 10,
    "HASHTAGS": ["#RedditStories", "#Reddit", "#Shorts", "#StoryTime", "#FunnyStories"]
}

MAX_SHORT_LENGTH = 59  # seconds

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

def get_duration(path):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])

def split_video(path):
    duration = get_duration(path)
    parts = []

    if duration <= MAX_SHORT_LENGTH:
        return [path]

    base = Path(path)
    total_parts = int(duration // MAX_SHORT_LENGTH) + 1

    for i in range(total_parts):
        start = i * MAX_SHORT_LENGTH
        out = base.parent / f"{base.stem}_part{i+1}.mp4"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(path),
            "-ss", str(start),
            "-t", str(MAX_SHORT_LENGTH),
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-c:a", "aac",
            str(out)
        ]

        subprocess.run(cmd, check=True)
        parts.append(str(out))

    return parts

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

    # Find all MP4s in the folder
    mp4_files = sorted(list(clips_root.glob("*.mp4")))

    if not mp4_files:
        logging.info("No videos found. Exiting.")
        return

    original = mp4_files[0]
    
    # Split if needed
    parts = split_video(str(original))

    # Track which part to upload
    progress_file = Path("part_progress.json")

    if progress_file.exists():
        progress = json.loads(progress_file.read_text())
    else:
        progress = {}

    next_part = None

    for p in parts:
        if p not in progress.get(original.name, []):
            next_part = p
            break

    if not next_part:
        # all parts uploaded → move original away
        original.rename(uploaded_root / original.name)
        progress.pop(original.name, None)
        progress_file.write_text(json.dumps(progress))
        logging.info("All parts uploaded for this video.")
        return

    # Build title
    part_index = parts.index(next_part) + 1
    total = len(parts)

    title = clean_title(original.stem)
    if total > 1:
        title += f" (Part {part_index}/{total})"

    description = "Reddit story\n\n" + " ".join(CONFIG["HASHTAGS"])

    success = upload_video(youtube, title, description, next_part)
    
    if success:
        progress.setdefault(original.name, []).append(next_part)
        progress_file.write_text(json.dumps(progress))

        logging.info(f"Uploaded {next_part}")



# ---------------------------------
# ENTRY POINT
# ---------------------------------
if __name__ == "__main__":
    logging.info("Starting YouTube Shorts uploader (single-run)...")
    uploader_once()
