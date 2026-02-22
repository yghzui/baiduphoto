import os
import json
import sys
import argparse
import shutil
import time
import threading
import queue
from concurrent.futures import ThreadPoolExecutor

# Add current directory to path
sys.path.append(os.getcwd())

from pybaiduphoto import API

class MultiLinePrinter:
    def __init__(self, num_lines):
        self.num_lines = num_lines
        self.lines = ["Waiting..."] * num_lines
        self.lock = threading.Lock()
        # Reserve space
        sys.stdout.write("\n" * num_lines)
        self._move_up(num_lines)

    def _move_up(self, n):
        if n > 0:
            sys.stdout.write(f"\033[{n}A")

    def update(self, slot_id, text):
        with self.lock:
            if 0 <= slot_id < self.num_lines:
                self.lines[slot_id] = text
                self._redraw()

    def log(self, text):
        with self.lock:
            # Move cursor to start of progress block
            # (Assumes cursor is currently at bottom of block)
            self._move_up(self.num_lines)
            # Clear everything below
            sys.stdout.write("\033[J")
            # Print log
            sys.stdout.write(f"{text}\n")
            # Print progress lines again
            for line in self.lines:
                sys.stdout.write(f"{line}\033[K\n")
            sys.stdout.flush()

    def _redraw(self):
        self._move_up(self.num_lines)
        for line in self.lines:
            sys.stdout.write(f"{line}\033[K\n")
        sys.stdout.flush()

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

def upload_folder_task(folder_path, album_name=None, cookie_file='cookies.json', max_retries=3, local_check=True, block_size_mb=4, num_threads=1, only_upload=False):
    print(f"--- Starting upload task for folder: {folder_path} ---")
    
    # Calculate block size in bytes
    block_size = int(block_size_mb * 1024 * 1024)
    print(f"Upload block size: {block_size_mb} MB ({block_size} bytes)")
    print(f"Number of threads: {num_threads}")
    if only_upload:
        print("Mode: Only upload (Skip adding to album)")
    
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
    target_album = None
    if not only_upload:
        print(f"Checking album existence: {album_name}...")
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
    else:
        print("Skipping album creation/lookup as per --only_upload.")

    # 5. Get existing files in album for de-duplication
    existing_names = set()
    if not only_upload and target_album:
        print("Fetching existing files in album to skip duplicates...")
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
    else:
         print("Skipping album content check (only_upload mode or no album found).")

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
    processed_count = 0
    lock = threading.Lock()
    
    # Initialize MultiLinePrinter
    printer = MultiLinePrinter(num_threads)
    
    # Slot Queue for managing display lines
    slot_queue = queue.Queue()
    for i in range(num_threads):
        slot_queue.put(i)

    # Helper to save history
    def save_progress():
        if local_check:
            try:
                # Reload to minimize overwrite risk
                current_full = load_history(history_file)
                current_full[album_name] = list(album_history.keys())
                save_history(current_full, history_file)
            except Exception as e:
                printer.log(f"Warning: Failed to save history: {e}")

    # Worker Function
    def process_file(task_item):
        nonlocal upload_count, skip_count, fail_count, processed_count
        idx, root, file = task_item
        
        # Get a display slot
        slot_id = slot_queue.get()
        task_start_time = time.time()
        
        try:
            # Calculate progress safely
            with lock:
                processed_count += 1
                current_progress = processed_count
                
            file_path = os.path.join(root, file)
            
            # Helper to format elapsed time
            def get_elapsed_str():
                elapsed = int(time.time() - task_start_time)
                hours, remainder = divmod(elapsed, 3600)
                minutes, seconds = divmod(remainder, 60)
                return f"{hours:02}:{minutes:02}:{seconds:02}"

            # Clean filename to handle emojis (filter out non-BMP characters)
            cleaned_name = clean_filename(file)
            
            # --- Check 1: Local History (if enabled) ---
            should_skip = False
            with lock:
                 if local_check and cleaned_name in album_history:
                     should_skip = True
            
            if should_skip:
                 with lock:
                     skip_count += 1
                 # Optional: Log skip if needed, but keeping it quiet for speed unless error
                 # printer.log(f"[{current_progress}/{total_files}] [Skip-Local] {file}")
                 printer.update(slot_id, f"[ID:{slot_id}] [{current_progress}/{total_files}] [Skip-Local] {file}")
                 time.sleep(0.1) # Brief pause to show skip
                 return

            # --- Check 2: Cloud Existing ---
            should_skip_cloud = False
            with lock:
                if cleaned_name in existing_names:
                    should_skip_cloud = True
            
            if should_skip_cloud:
                with lock:
                    if local_check and cleaned_name not in album_history:
                         album_history[cleaned_name] = True
                    skip_count += 1
                printer.update(slot_id, f"[ID:{slot_id}] [{current_progress}/{total_files}] [Skip-Cloud] {file}")
                time.sleep(0.1)
                return
            
            printer.update(slot_id, f"[ID:{slot_id}] [{current_progress}/{total_files}] [Start] {file}")
            
            # Prepare for upload (renaming logic)
            upload_path = file_path
            temp_path = None
            
            # If filename needs cleaning (contains emojis/special chars)
            if file != cleaned_name:
                printer.log(f"  -> Cleaning filename: {file} -> {cleaned_name}")
                
                # Check for local collision
                candidate_path = os.path.join(root, cleaned_name)
                if os.path.exists(candidate_path):
                    root_name, ext = os.path.splitext(cleaned_name)
                    cleaned_name_safe = f"{root_name}_safe{ext}"
                    candidate_path = os.path.join(root, cleaned_name_safe)
                    printer.log(f"  -> Collision detected. Trying: {cleaned_name_safe}")
                    
                    if os.path.exists(candidate_path):
                        with lock:
                            fail_count += 1
                        printer.log(f"  -> Error: Safe filename also exists. Skipping {file}.")
                        return
                    cleaned_name = cleaned_name_safe

                temp_path = candidate_path
                try:
                    try:
                        os.link(file_path, temp_path)
                    except OSError:
                        shutil.copy2(file_path, temp_path)
                    upload_path = temp_path
                except Exception as e:
                    with lock:
                        fail_count += 1
                    printer.log(f"  -> Error creating temp file: {e}")
                    return

            # Retry Loop
            success = False
            for attempt in range(max_retries):
                if attempt > 0:
                    printer.log(f"  -> Retry attempt {attempt+1}/{max_retries} for {file}...")
                    time.sleep(2)
                
                # Progress Callback Factory
                def create_progress_callback(filename, my_slot):
                    start_time = time.time()
                    last_print_time = 0
                    
                    def callback(uploaded_size, total_size):
                        nonlocal last_print_time
                        current_time = time.time()
                        
                        # Update every 0.5 second or if finished
                        if current_time - last_print_time >= 0.5 or uploaded_size >= total_size:
                            elapsed_seconds = current_time - start_time
                            elapsed = int(elapsed_seconds)
                            hours, remainder = divmod(elapsed, 3600)
                            minutes, seconds = divmod(remainder, 60)
                            time_str = f"{hours:02}:{minutes:02}:{seconds:02}"
                            
                            uploaded_mb = uploaded_size / (1024 * 1024)
                            total_mb = total_size / (1024 * 1024)
                            
                            percent = (uploaded_size / total_size) * 100 if total_size > 0 else 0
                            speed = uploaded_mb / elapsed_seconds if elapsed_seconds > 0.1 else 0
                            
                            # Calculate ETA
                            remaining_mb = total_mb - uploaded_mb
                            if speed > 0:
                                eta_seconds = int(remaining_mb / speed)
                                eta_h, eta_r = divmod(eta_seconds, 3600)
                                eta_m, eta_s = divmod(eta_r, 60)
                                eta_str = f"{eta_h:02}:{eta_m:02}:{eta_s:02}"
                            else:
                                eta_str = "--:--:--"

                            # [ID:X] [N/Total] [Upload] 12.5/100.0(MB) 12.5% 耗时00:00:12 1.50MB/s 剩余00:00:05 filename
                            msg = f"[ID:{my_slot}] [{current_progress}/{total_files}] [Upload] {uploaded_mb:.2f}/{total_mb:.2f}(MB) {percent:.1f}% 耗时{time_str} {speed:.2f}MB/s 剩余{eta_str} {filename}"
                            printer.update(my_slot, msg)
                                
                            last_print_time = current_time
                    return callback
                
                progress_cb = create_progress_callback(file, slot_id)

                try:
                    ret = api.upload_1file(filePath=upload_path, album=target_album, progress_callback=progress_cb, block_size=block_size)
                    
                    if ret:
                        is_existing = getattr(ret, 'is_existing', False)
                        with lock:
                            if is_existing:
                                # printer.log(f"  -> [Cloud Match] File exists in cloud (fs_id={ret.get_fsid()}).")
                                pass # Keep log quiet
                            
                            append_res = getattr(ret, 'append_result', None)
                            # Verify album addition
                            
                            existing_names.add(cleaned_name) 
                            if local_check:
                                album_history[cleaned_name] = True
                            
                            upload_count += 1
                        
                        success = True
                        printer.log(f"[{current_progress}/{total_files}] [Success] 耗时:{get_elapsed_str()} {file}")
                        break
                    else:
                        printer.log(f"[{current_progress}/{total_files}] [Failed] {file} (API returned None)")
                except Exception as e:
                    printer.log(f"[{current_progress}/{total_files}] [Error] {file}: {e}")

            if not success:
                with lock:
                    fail_count += 1
                printer.log(f"[{current_progress}/{total_files}] [Failed] All {max_retries} attempts failed for {file}")

            # Cleanup temp file
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError as e:
                    printer.log(f"  -> Warning: Failed to remove temp file {temp_path}: {e}")

            # Periodic Save
            with lock:
                if processed_count % 20 == 0:
                    save_progress()
                    printer.log(f"  -> [System] Periodic history save ({processed_count}/{total_files})")
        
        finally:
            # Clear the line before releasing slot (optional, or leave last status)
            # printer.update(slot_id, f"[ID:{slot_id}] Idle") 
            slot_queue.put(slot_id)
    
    # 7. Process files
    if num_threads <= 1:
        # Single Thread Mode (keep original loop style mostly)
        for idx, (root, file) in enumerate(file_tasks):
            process_file((idx, root, file))
    else:
        # Multi-Thread Mode
        print(f"Starting execution with {num_threads} threads...")
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            # Create task items
            tasks = []
            for idx, (root, file) in enumerate(file_tasks):
                tasks.append((idx, root, file))
            executor.map(process_file, tasks)
    
    # Final Save
    
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
    parser.add_argument("--block_size", type=float, default=10, help="Upload block size in MB (default: 20)")
    parser.add_argument("--threads", type=int, default=4, help="Number of upload threads (default: 3)")
    parser.add_argument("--only_upload", action="store_true", help="Only upload files without adding to specified album.")
     
    args = parser.parse_args()
    
    target_folder = args.folder_input if args.folder_input else args.folder_path
    local_check = args.local_check if args.local_check is not None else True

    if not target_folder:
        print("Error: You must provide a folder path.")
        parser.print_help()
    else:
        upload_folder_task(target_folder, args.album_name, args.cookie_file, args.retries, local_check, args.block_size, args.threads, only_upload=args.only_upload)
