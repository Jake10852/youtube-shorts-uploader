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

def split_video(path):
    """
    Splits the video into MAX_SHORT_LENGTH chunks.
    Returns a list of paths to the split parts.
    """
    duration = get_duration(path)
    base = Path(path)
    parts_dir = base.parent / "TempParts"
    parts_dir.mkdir(exist_ok=True)

    parts = []

    if duration <= MAX_SHORT_LENGTH:
        logging.info("Video <=59s – no split needed")
        parts.append(str(base))
        return parts

    total_parts = int(duration // MAX_SHORT_LENGTH) + 1

    for i in range(total_parts):
        out = parts_dir / f"{base.stem}_part{i+1}.mp4"
        if out.exists():
            # reuse existing part
            parts.append(str(out))
            continue

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

        logging.info(f"Splitting part {i+1}/{total_parts}: {' '.join(cmd)}")
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

    PROGRESS_FILE = Path("part_progress.json")
    if progress_file.exists():
        with open(PROGRESS_FILE, "r") as f:
            progress = json.load(f)
    else:
        progress = {}

    # Find all videos that are not fully uploaded
    videos = sorted(list(clips_root.glob("*.mp4")))
    if not videos:
        logging.info("No videos found. Exiting.")
        return

    for video in videos:
        parts = split_video(str(video))
        uploaded_indices = progress.get(video.name, [])

        # Find next part to upload
        next_index = None
        for i, _ in enumerate(parts, start=1):
            if i not in uploaded_indices:
                next_index = i
                break

        if next_index is None:
            # all parts uploaded → move original away and remove temp parts
            video.rename(uploaded_root / video.name)
            temp_dir = video.parent / "TempParts"
            if temp_dir.exists():
                for f in temp_dir.glob(f"{video.stem}_part*.mp4"):
                    f.unlink()
            progress.pop(video.name, None)
            progress_file.write_text(json.dumps(progress))
            logging.info(f"All parts uploaded for {video.name}. Moved to Uploaded folder.")
            continue

        # Upload the next part
        next_part_path = parts[next_index - 1]
        title = clean_title(video.stem)
        if len(parts) > 1:
            title += f" (Part {next_index}/{len(parts)})"

        description = "Reddit story\n\n" + " ".join(CONFIG["HASHTAGS"])

        success = upload_video(youtube, title, description, next_part_path)
        if success:
            uploaded_indices.append(next_index)
            progress[video.name] = uploaded_indices
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
