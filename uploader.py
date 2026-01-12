def uploader_once():
    youtube = get_authenticated_service()
    clips_root = Path(CONFIG["CLIPS_DIR"])
    uploaded_root = Path(CONFIG["UPLOADED_DIR"])
    uploaded_root.mkdir(exist_ok=True)

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
