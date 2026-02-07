import os
import json
import sys
import argparse
import shutil
import time

# Add current directory to path
sys.path.append(os.getcwd())

from pybaiduphoto import API

def load_history(history_file):
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load history from {history_file}: {e}")
            return {}
    return {}

def save_history(history, history_file):
    try:
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: Failed to save local history to {history_file}: {e}")

def clean_filename(text):
    """
    Remove characters outside the Basic Multilingual Plane (BMP) to filter out emojis
    that might cause API or encoding errors.
    """
    return "".join(c for c in text if c <= "\uFFFF")

def get_history_filename(cookie_file):
    base = os.path.basename(cookie_file)
    if base == 'cookies.json':
        return 'local_upload_history.json'
    
    stem = os.path.splitext(base)[0]
    return f'local_upload_history_{stem}.json'

def upload_folder_task(folder_path, album_name=None, cookie_file='cookies.json', max_retries=3, local_check=True, block_size_mb=4):
    print(f"--- Starting upload task for folder: {folder_path} ---")
    
    # Calculate block size in bytes
    block_size = int(block_size_mb * 1024 * 1024)
    print(f"Upload block size: {block_size_mb} MB ({block_size} bytes)")
    
    # 1. Validate folder
    if not os.path.exists(folder_path):
        print(f"Error: Upload directory does not exist: {folder_path}")
        return

    # 2. Determine Album Name
    if not album_name or album_name.lower() == 'none':
        album_name = os.path.basename(os.path.normpath(folder_path))
        print(f"Album name not specified (or 'None'). Using folder name: {album_name}")
    else:
        print(f"Target Album: {album_name}")

    # Determine history file
    history_file = get_history_filename(cookie_file)
    print(f"Using history file: {history_file}")

    # Load local history if enabled
    full_history = {}
    album_history = {} # Set of cleaned names
    if local_check:
        print("Loading local upload history...")
        full_history = load_history(history_file)
        # Ensure we have a dict/set for the current album
        raw_list = full_history.get(album_name, [])
        # Convert to dict for fast lookup (using dict as ordered set)
        if isinstance(raw_list, list):
            album_history = {name: True for name in raw_list}
        else:
            album_history = raw_list # Assume it's a dict if not list

    # 3. Login
    cookie_path = cookie_file
    if not os.path.isabs(cookie_path):
        cookie_path = os.path.join(os.getcwd(), cookie_file)

    if not os.path.exists(cookie_path):
        print(f"Error: Cookie file not found at {cookie_path}")
        return
        
    try:
        with open(cookie_path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        api = API(cookies=cookies)
        print(f"API initialized successfully using {cookie_file}.")
    except Exception as e:
        print(f"Login failed: {e}")
        return

    # 4. Find or Create Album
    print(f"Checking album existence: {album_name}...")
    target_album = None
    try:
        all_albums = api.get_self_All(typeName='Album')
        for album in all_albums:
            if album.getName() == album_name:
                target_album = album
                print(f"Found existing album: {album_name} (ID: {album.getID()})")
                break
        
        if not target_album:
            print(f"Album not found. Creating new album: {album_name}...")
            target_album = api.createNewAlbum(Name=album_name)
            print(f"Album created successfully: {album_name} (ID: {target_album.getID()})")
    except Exception as e:
        print(f"Error handling album creation: {e}")
        return

    # 5. Get existing files in album for de-duplication
    print("Fetching existing files in album to skip duplicates...")
    existing_names = set()
    try:
        # get_sub_All returns a list of OnlineItem
        existing_items = target_album.get_sub_All()
        if existing_items:
            for item in existing_items:
                existing_names.add(item.getName())
        print(f"Found {len(existing_names)} existing files in album.")
    except Exception as e:
        print(f"Error fetching album content: {e}")
        return

    # 6. Collect all files
    print(f"Scanning upload directory: {folder_path}")
    file_tasks = []
    
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            file_path = os.path.join(root, file)
            if file.endswith(('.aria2',".done")):
                try:
                    os.remove(file_path)
                    print(f"  -> Removed: {file_path} (Aria2 or done file)")
                except Exception as e:
                    print(f"  -> Warning: Failed to remove {file}: {e}")
                continue
            
            file_tasks.append((root, file))

    # Sort files by size (ascending) - upload small files first
    print("Sorting files by size (small to large)...")
    file_tasks.sort(key=lambda x: os.path.getsize(os.path.join(x[0], x[1])))

    total_files = len(file_tasks)
    print(f"Total files to process: {total_files}")

    upload_count = 0
    skip_count = 0
    fail_count = 0

    # Helper to save history
    def save_progress():
        if local_check:
            try:
                # Reload to minimize overwrite risk
                current_full = load_history(history_file)
                current_full[album_name] = list(album_history.keys())
                save_history(current_full, history_file)
            except Exception as e:
                print(f"Warning: Failed to save history: {e}")

    # 7. Process files
    for idx, (root, file) in enumerate(file_tasks):
        current_progress = idx + 1
        print(f"\nProgress [{current_progress}/{total_files}] | Success: {upload_count} | Skipped: {skip_count} | Failed: {fail_count}")
        
        file_path = os.path.join(root, file)
        
        # Clean filename to handle emojis (filter out non-BMP characters)
        cleaned_name = clean_filename(file)
        
        # --- Check 1: Local History (if enabled) ---
        if local_check and cleaned_name in album_history:
             print(f"[Skip-Local] {file} (Found in local history)")
             skip_count += 1
             continue

        # --- Check 2: Cloud Existing ---
        if cleaned_name in existing_names:
            print(f"[Skip-Cloud] {file} (Already exists as {cleaned_name})")
            if local_check and cleaned_name not in album_history:
                 album_history[cleaned_name] = True
            skip_count += 1
            continue
        
        print(f"[Upload] {file} ...")
        
        # Prepare for upload (renaming logic)
        upload_path = file_path
        temp_path = None
        
        # If filename needs cleaning (contains emojis/special chars)
        if file != cleaned_name:
            print(f"  -> Cleaning filename: {file} -> {cleaned_name}")
            
            # Check for local collision
            candidate_path = os.path.join(root, cleaned_name)
            if os.path.exists(candidate_path):
                root_name, ext = os.path.splitext(cleaned_name)
                cleaned_name_safe = f"{root_name}_safe{ext}"
                candidate_path = os.path.join(root, cleaned_name_safe)
                print(f"  -> Collision detected. Trying: {cleaned_name_safe}")
                
                if os.path.exists(candidate_path):
                    print(f"  -> Error: Safe filename also exists. Skipping.")
                    fail_count += 1
                    continue
                cleaned_name = cleaned_name_safe

            temp_path = candidate_path
            try:
                try:
                    os.link(file_path, temp_path)
                except OSError:
                    shutil.copy2(file_path, temp_path)
                upload_path = temp_path
            except Exception as e:
                print(f"  -> Error creating temp file: {e}")
                fail_count += 1
                continue

        # Retry Loop
        success = False
        for attempt in range(max_retries):
            if attempt > 0:
                print(f"  -> Retry attempt {attempt+1}/{max_retries}...")
                time.sleep(2)
            
            # Progress Callback Factory
            def create_progress_callback(filename):
                start_time = time.time()
                last_print_time = 0
                
                def callback(uploaded_size, total_size):
                    nonlocal last_print_time
                    current_time = time.time()
                    
                    # Update every 1 second or if finished
                    if current_time - last_print_time >= 1 or uploaded_size >= total_size:
                        elapsed = int(current_time - start_time)
                        hours, remainder = divmod(elapsed, 3600)
                        minutes, seconds = divmod(remainder, 60)
                        time_str = f"{hours:02}:{minutes:02}:{seconds:02}"
                        
                        uploaded_mb = uploaded_size / (1024 * 1024)
                        total_mb = total_size / (1024 * 1024)
                        
                        # [Upload] 12.5/100.0(MB) 耗时00:00:12 filename
                        msg = f"\r[Upload] {uploaded_mb:.2f}/{total_mb:.2f}(MB) 耗时{time_str} {filename}"
                        sys.stdout.write(msg)
                        sys.stdout.flush()
                        last_print_time = current_time
                return callback

            try:
                progress_cb = create_progress_callback(file)
                ret = api.upload_1file(filePath=upload_path, album=target_album, progress_callback=progress_cb, block_size=block_size)
                
                # Clear progress line and print newline after completion
                sys.stdout.write("\n") 
                
                if ret:
                    if getattr(ret, 'is_existing', False):
                        print(f"  -> [Cloud Match] File exists in cloud (fs_id={ret.get_fsid()}).")
                        append_res = getattr(ret, 'append_result', None)
                        if append_res and append_res.get('errno') in [0, 50000]:
                            print(f"  -> [Album] Verified in/added to album.")
                        else:
                            print(f"  -> [Album] Warning: Status unknown. Result: {append_res}")
                    else:
                        print(f"  -> [Upload] Success (New file).")
                    
                    existing_names.add(cleaned_name) 
                    if local_check:
                        album_history[cleaned_name] = True
                    
                    upload_count += 1
                    success = True
                    break
                else:
                    print(f"  -> Failed (API returned None)")
            except Exception as e:
                print(f"  -> Error: {e}")

        if not success:
            print(f"  -> All {max_retries} attempts failed for {file}")
            fail_count += 1

        # Cleanup temp file
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError as e:
                print(f"  -> Warning: Failed to remove temp file {temp_path}: {e}")

        # Periodic Save
        if (idx + 1) % 20 == 0:
            save_progress()
            print(f"  -> [System] Periodic history save ({idx+1}/{total_files})")
    
    # Final Save
    save_progress()
    if local_check:
        print(f"Local history updated for album: {album_name} (File: {history_file})")

    print("\n--- Task Summary ---")
    print(f"Total:    {total_files}")
    print(f"Uploaded: {upload_count}")
    print(f"Skipped:  {skip_count}")
    print(f"Failed:   {fail_count}")
    
    if upload_count + skip_count + fail_count != total_files:
        print(f"Warning: Count mismatch! ({upload_count}+{skip_count}+{fail_count} != {total_files})")

if __name__ == '__main__':
    folder_path = r"I:\20260118珠海"
    album_name = None#"20251108_09大鹏半岛深圳湾"
    cookie_file = "cookies_new.json"
    parser = argparse.ArgumentParser(description="Upload a folder to a Baidu Photo album.")
    parser.add_argument("folder_input", nargs='?', default=None, help="Local folder path to upload")
    parser.add_argument("--folder_path", default=folder_path, type=str, help="Local folder path (optional)")
    parser.add_argument("--album_name", default=album_name, help="Target album name. Defaults to folder name if not specified or 'None'.")
    parser.add_argument("--cookie_file", default=cookie_file, help=f"Path to cookie file (default: {cookie_file})")
    parser.add_argument("--retries", type=int, default=3, help="Max retries for failed uploads (default: 3)")
    parser.add_argument("--local_check", default=True, type=lambda x: (str(x).lower() == 'true'), help="Enable local history verification to skip uploaded files")
    parser.add_argument("--block_size", type=float, default=100, help="Upload block size in MB (default: 4)")
     
    args = parser.parse_args()
    
    target_folder = args.folder_input if args.folder_input else args.folder_path
    local_check = args.local_check if args.local_check is not None else True

    if not target_folder:
        print("Error: You must provide a folder path.")
        parser.print_help()
    else:
        upload_folder_task(target_folder, args.album_name, args.cookie_file, args.retries, local_check, args.block_size)
