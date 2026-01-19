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

PROGRESS_FILE = Path("part_progress.json")
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

    logging.info(f"FFPROBE OUTPUT: {result.stdout}")
    logging.info(f"FFPROBE ERROR: {result.stderr}")

    try:
        data = json.loads(result.stdout)

        if "format" not in data:
            logging.warning("No format key in ffprobe output – assuming 59s")
            return 59

        return float(data["format"].get("duration", 59))

    except Exception as e:
        logging.warning(f"Could not read duration: {e}")
        return 59  # safe fallback

def get_video_parts(video_path):
    """Return a list of split part paths, creating them only if they don't exist."""
    base = Path(video_path)
    parts_dir = base.parent / "TempParts"
    parts_dir.mkdir(exist_ok=True)

    # check for existing split parts
    existing_parts = sorted(parts_dir.glob(f"{base.stem}_part*.mp4"))
    if existing_parts:
        return [str(p) for p in existing_parts]

    # no parts yet, split video
    duration = get_duration(str(base))
    total_parts = int(duration // MAX_SHORT_LENGTH) + 1
    parts = []

    for i in range(total_parts):
        out = parts_dir / f"{base.stem}_part{i+1}.mp4"
        start = i * MAX_SHORT_LENGTH
        cmd = [
            "ffmpeg", "-y",
            "-i", str(base),
            "-ss", str(start),
            "-t", str(MAX_SHORT_LENGTH),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-c:a", "aac",
            "-movflags", "+faststart",
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
    global progress
    youtube = get_authenticated_service()
    clips_root = Path(CONFIG["CLIPS_DIR"])
    uploaded_root = Path(CONFIG["UPLOADED_DIR"])
    uploaded_root.mkdir(exist_ok=True)

    
    videos = sorted(list(clips_root.glob("*.mp4")))
    if not videos:
        logging.info("No videos found. Exiting.")
        return

    for video in videos:
        parts = get_video_parts(str(video))
        uploaded_indices = progress.get(video.name, [])

        # find next part to upload
        next_index = None
        for i in range(1, len(parts)+1):
            if i not in uploaded_indices:
                next_index = i
                break

        if next_index is None:
            # all parts uploaded → move original and cleanup
            ...
            continue

        # upload next part
        next_part_path = parts[next_index-1]
        title = clean_title(video.stem)
        if len(parts) > 1:
            title += f" (Part {next_index}/{len(parts)})"
        description = "Reddit story\n\n" + " ".join(CONFIG["HASHTAGS"])

        success = upload_video(youtube, title, description, next_part_path)
        if success:
            uploaded_indices.append(next_index)
            progress[video.name] = uploaded_indices

            # -----------------------------
            # SAVE PROGRESS HERE
            # -----------------------------
            with open(PROGRESS_FILE, "w") as f:
                json.dump(progress, f)

            logging.info(f"Uploaded {next_part_path} (Part {next_index}/{len(parts)})")
        break  # upload only one part per run

# ---------------------------------
# ENTRY POINT
# ---------------------------------
if __name__ == "__main__":
    logging.info("Starting YouTube Shorts uploader (single-run)...")
    uploader_once()
