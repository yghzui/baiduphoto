import os
import json
import sys
import argparse
import shutil
import time

# Add current directory to path
sys.path.append(os.getcwd())

from pybaiduphoto import API
from generate_upload_info import generate_upload_info

HISTORY_FILE = 'local_upload_history.json'

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load history: {e}")
            return {}
    return {}

def save_history(history):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: Failed to save local history: {e}")

def clean_filename(text):
    """
    Remove characters outside the Basic Multilingual Plane (BMP) to filter out emojis
    that might cause API or encoding errors.
    """
    return "".join(c for c in text if c <= "\uFFFF")

def upload_task(user_id_or_name, max_retries=3, local_check=False):
    print(f"--- Starting upload task for: {user_id_or_name} ---")
    
    # 1. Get info
    album_name, upload_dir = generate_upload_info(user_id_or_name)
    if not album_name or not upload_dir:
        print("Failed to generate upload info. Please check if the user exists in users.json.")
        return

    if not os.path.exists(upload_dir):
        print(f"Error: Upload directory does not exist: {upload_dir}")
        return

    # Load local history if enabled
    full_history = {}
    album_history = {} # Set of cleaned names
    if local_check:
        print("Loading local upload history...")
        full_history = load_history()
        # Ensure we have a dict/set for the current album
        raw_list = full_history.get(album_name, [])
        # Convert to dict for fast lookup (using dict as ordered set)
        if isinstance(raw_list, list):
             album_history = {name: True for name in raw_list}
        else:
             album_history = raw_list # Assume it's a dict if not list

    # 2. Login
    cookie_path = os.path.join(os.getcwd(), 'cookies.json')
    if not os.path.exists(cookie_path):
        print(f"Error: cookies.json not found at {cookie_path}")
        return
        
    try:
        with open(cookie_path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        api = API(cookies=cookies)
        print("API initialized successfully.")
    except Exception as e:
        print(f"Login failed: {e}")
        return

    # 3. Find or Create Album
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

    # 4. Get existing files in album for de-duplication
    # Even if local_check is True, we should probably still fetch cloud state 
    # unless we are strictly relying on local. But the requirement says 
    # "if enabled, local check then skip directly". 
    # We still fetch existing_names to update our local history if needed and to skip duplicates 
    # that are in cloud but not in local history.
    print("Fetching existing files in album to skip duplicates...")
    existing_names = set()
    try:
        # get_sub_All returns a list of OnlineItem
        existing_items = target_album.get_sub_All()
        if existing_items:
            for item in existing_items:
                existing_names.add(item.getName())
        print(f"Found {len(existing_names)} existing files in album.")
        
        # Sync cloud existing to local history if not present?
        # User requirement: "Must be confirmed uploaded". 
        # Cloud existing means it IS uploaded. So we can implicitly trust it.
    except Exception as e:
        print(f"Error fetching album content: {e}")
        return

    # 5. Collect all files first
    print(f"Scanning upload directory: {upload_dir}")
    file_tasks = []
    
    for root, dirs, files in os.walk(upload_dir):
        for file in files:
            file_path = os.path.join(root, file)
            if file.endswith('.aria2'):
                try:
                    os.remove(file_path)
                    print(f"  -> Removed: {file_path} (Aria2 file)")
                except Exception as e:
                    print(f"  -> Warning: Failed to remove {file}: {e}")
                continue
            
            file_tasks.append((root, file))

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
                current_full = load_history()
                current_full[album_name] = list(album_history.keys())
                save_history(current_full)
            except Exception as e:
                print(f"Warning: Failed to save history: {e}")

    # 6. Process files
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
            # If in cloud but not in local history, should we add it?
            # User said "Add uploaded file record". If we confirm it's in cloud, we can record it.
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
            
            # Check for local collision (if the cleaned filename already exists locally)
            candidate_path = os.path.join(root, cleaned_name)
            if os.path.exists(candidate_path):
                # Collision detected, try to append safe suffix
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
                # Try hardlink first (fast, no space used), then copy
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
                time.sleep(2) # Wait a bit before retry
            
            try:
                # upload_1file handles uploading and appending to album
                ret = api.upload_1file(filePath=upload_path, album=target_album)
                if ret:
                    if getattr(ret, 'is_existing', False):
                        print(f"  -> [Cloud Match] File exists in cloud (fs_id={ret.get_fsid()}).")
                        # Check append result
                        append_res = getattr(ret, 'append_result', None)
                        if append_res and append_res.get('errno') in [0, 50000]: # 0=success, 50000=already in album
                            print(f"  -> [Album] Verified in/added to album.")
                        else:
                            print(f"  -> [Album] Warning: Status unknown. Result: {append_res}")
                    else:
                        print(f"  -> [Upload] Success (New file).")
                    
                    # Update States
                    existing_names.add(cleaned_name) 
                    if local_check:
                        album_history[cleaned_name] = True
                    
                    upload_count += 1
                    success = True
                    break # Break retry loop
                else:
                    print(f"  -> Failed (API returned None)")
                    # Don't break, retry
            except Exception as e:
                print(f"  -> Error: {e}")
                # Don't break, retry

        if not success:
            print(f"  -> All {max_retries} attempts failed for {file}")
            fail_count += 1

        # Cleanup temp file if created
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError as e:
                print(f"  -> Warning: Failed to remove temp file {temp_path}: {e}")

        # Periodic Save
        if (idx + 1) % 20 == 0:
            save_progress()
            print(f"  -> [System] Periodic history save ({idx+1}/{total_files})")
    
    # End of loop
    
    # Final Save
    save_progress()
    if local_check:
        print(f"Local history updated for album: {album_name}")

    print("\n--- Task Summary ---")
    print(f"Total:    {total_files}")
    print(f"Uploaded: {upload_count}")
    print(f"Skipped:  {skip_count}")
    print(f"Failed:   {fail_count}")
    
    # Validation
    if upload_count + skip_count + fail_count != total_files:
        print(f"Warning: Count mismatch! ({upload_count}+{skip_count}+{fail_count} != {total_files})")
    
    return {
        'name': user_id_or_name,
        'total': total_files,
        'uploaded': upload_count,
        'skipped': skip_count,
        'failed': fail_count
    }

def upload_album(user_id_or_name, max_retries=3, local_check=False):
    # Check for 'all' command
    if user_id_or_name == 'all':
        print("--- Batch Upload Mode: Processing ALL users from users.json ---")
        users_path = os.path.join(os.getcwd(), 'users.json')
        if os.path.exists(users_path):
            try:
                with open(users_path, 'r', encoding='utf-8') as f:
                    users = json.load(f)
                
                total_users = len(users)
                print(f"Found {total_users} users in users.json")
                
                summary_report = []

                for i, user in enumerate(users):
                    user_id = user.get('id')
                    name = user.get('name')
                    target = user_id if user_id else name
                    
                    if target:
                        print(f"\n[{i+1}/{total_users}] Processing user: {name} (ID: {user_id})")
                        stats = upload_task(target, max_retries, local_check)
                        if stats:
                            # If upload_task returns None (e.g. invalid user info), handle it
                            # But current upload_task returns None on early exit. 
                            # We need to ensure upload_task always returns a dict or handle None.
                            # I will update upload_task to return None on early error, so we handle it here.
                            stats['user_display'] = f"{name}({user_id})" if name and user_id else target
                            summary_report.append(stats)
                    else:
                        print(f"\n[{i+1}/{total_users}] Skipping invalid user entry: {user}")
                
                # Print Final Summary
                print("\n" + "="*60)
                print(f"{'FINAL BATCH UPLOAD SUMMARY':^60}")
                print("="*60)
                print(f"{'User':<25} | {'Total':<8} | {'Upload':<8} | {'Skip':<8} | {'Fail':<8}")
                print("-" * 60)
                
                total_files_all = 0
                total_uploaded_all = 0
                total_skipped_all = 0
                total_failed_all = 0

                for s in summary_report:
                    print(f"{s['user_display']:<25} | {s['total']:<8} | {s['uploaded']:<8} | {s['skipped']:<8} | {s['failed']:<8}")
                    total_files_all += s['total']
                    total_uploaded_all += s['uploaded']
                    total_skipped_all += s['skipped']
                    total_failed_all += s['failed']
                
                print("-" * 60)
                print(f"{'TOTAL':<25} | {total_files_all:<8} | {total_uploaded_all:<8} | {total_skipped_all:<8} | {total_failed_all:<8}")
                print("="*60)

            except Exception as e:
                print(f"Error reading users.json: {e}")
        else:
            print(f"Error: users.json not found at {users_path}")
    else:
        print(f"\n>>> Processing argument: {user_id_or_name}")
        upload_task(user_id_or_name, max_retries, local_check)
        
        
if __name__ == '__main__':
    id_or_name="all"
    parser = argparse.ArgumentParser(description="Upload images to a specified album.")
    # Allow positional argument for backward compatibility and ease of use
    parser.add_argument("user_input", nargs='?', default=None, help="User ID or Name to upload images for. Use 'all' to process all users in users.json.")
    parser.add_argument("--user_id_or_name", default=id_or_name,type=str, help="User ID or Name (optional)")
    parser.add_argument("--retries", type=int, default=3, help="Max retries for failed uploads (default: 3)")
    parser.add_argument("--local_check", default=True, type=lambda x: (str(x).lower() == 'true'), help="Enable local history verification to skip uploaded files")
     
    args = parser.parse_args()
    
    # Prioritize positional argument, then flag, then default to "all"
    target = args.user_input if args.user_input else args.user_id_or_name
    local_check = args.local_check if args.local_check is not None else False
    if not target:
        target = "all"
        
    upload_album(target, args.retries, local_check)
