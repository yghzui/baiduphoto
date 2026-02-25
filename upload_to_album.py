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
# Ensure current script directory is in path for local imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from pybaiduphoto import API
from generate_upload_info import generate_upload_info
from file_time_utils import try_fix_file_time

current_dir = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(current_dir, 'local_upload_history.json')

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

def upload_task(user_id_or_name, max_retries=3, local_check=False, block_size_mb=4, num_threads=1, cookies_path=None, config_files=None):
    print(f"--- Starting upload task for: {user_id_or_name} ---")
    
    # Calculate block size in bytes
    block_size = int(block_size_mb * 1024 * 1024)
    print(f"Upload block size: {block_size_mb} MB ({block_size} bytes)")
    print(f"Number of threads: {num_threads}")

    # 1. Get info
    album_name, upload_dir = generate_upload_info(user_id_or_name, config_files)
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
    if not cookies_path:
         cookies_path = os.path.join(os.getcwd(), 'baiduphoto/cookies.json')
         
    if not os.path.exists(cookies_path):
        print(f"Error: Cookies file not found at {cookies_path}")
        return
        
    try:
        with open(cookies_path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        api = API(cookies=cookies)
        print(f"API initialized successfully with cookies from {cookies_path}")
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

    # 5. Collect all files first
    print(f"Scanning upload directory: {upload_dir}")
    file_tasks = []
    
    # 允许上传的文件扩展名 (图片, 视频, RAW, 其他)
    # 常见图片: .jpg, .jpeg, .png, .bmp, .gif, .webp, .heic, .tif, .tiff
    # 常见视频: .mp4, .mov, .avi, .mkv, .flv, .wmv, .m4v, .ts, .webm, .vob, .mts, .m2ts
    # 常见RAW: .arw, .cr2, .cr3, .nef, .dng, .orf, .raf, .rw2, .sr2, .srf, .srw, .nrw, .k25, .kdc, .dcs, .dcr, .drf, .obm, .pef, .ptx, .pxn, .r3d, .rwl, .rwz, .x3f, .3fr, .ari, .bay, .crw, .cap, .data, .eip, .erf, .fff, .gpr, .iiq, .mdc, .mef, .mos, .mrw
    # 其他: .lrf (DJI预览文件), .insv (Insta360全景)
    VALID_EXTENSIONS = {
        # 图片
        '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.heic', '.tif', '.tiff',
        # 视频
        '.mp4', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.m4v', '.ts', '.webm', '.vob', '.mts', '.m2ts',
        # RAW 格式 (覆盖主流相机品牌)
        '.arw', '.cr2', '.cr3', '.nef', '.dng', '.orf', '.raf', '.rw2', 
        '.sr2', '.srf', '.srw', '.nrw', '.k25', '.kdc', '.dcs', '.dcr', 
        '.drf', '.obm', '.pef', '.ptx', '.pxn', '.r3d', '.rwl', '.rwz', 
        '.x3f', '.3fr', '.ari', '.bay', '.crw', '.cap', '.data', '.eip', 
        '.erf', '.fff', '.gpr', '.iiq', '.mdc', '.mef', '.mos', '.mrw',
        # 其他设备特定格式
        '.lrf', # DJI 低分辨率预览
        '.insv' # Insta360 全景视频
    }

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
            
            # 2. 检查文件扩展名是否在白名单中
            ext = os.path.splitext(file)[1].lower()
            if ext not in VALID_EXTENSIONS:
                # print(f"  -> Skipped: {file} (Unsupported file type: {ext})")
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
                current_full = load_history()
                current_full[album_name] = list(album_history.keys())
                save_history(current_full)
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
                 printer.update(slot_id, f"[ID:{slot_id}] [{current_progress}/{total_files}] [Skip-Local] {file}")
                 time.sleep(0.1)
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

            # Fix upload file time (mtime) to match shoot time
            try:
                try_fix_file_time(upload_path)
            except Exception as e:
                pass

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
                    # upload_1file handles uploading and appending to album
                    # Updated to support block_size and progress_callback
                    ret = api.upload_1file(filePath=upload_path, album=target_album, progress_callback=progress_cb, block_size=block_size)
                    
                    if ret:
                        is_existing = getattr(ret, 'is_existing', False)
                        with lock:
                            if is_existing:
                                pass # Keep log quiet
                            
                            append_res = getattr(ret, 'append_result', None)
                            
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
            slot_queue.put(slot_id)

    # 6. Process files
    if num_threads <= 1:
        # Single Thread Mode
        for idx, (root, file) in enumerate(file_tasks):
            process_file((idx, root, file))
    else:
        # Multi-Thread Mode
        print(f"Starting execution with {num_threads} threads...")
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            tasks = []
            for idx, (root, file) in enumerate(file_tasks):
                tasks.append((idx, root, file))
            executor.map(process_file, tasks)
    
    # Final Save
    save_progress()
    if local_check:
        print(f"Local history updated for album: {album_name}")

    print("\n--- Task Summary ---")
    print(f"Total:    {total_files}")
    print(f"Uploaded: {upload_count}")
    print(f"Skipped:  {skip_count}")
    print(f"Failed:   {fail_count}")
    
    if upload_count + skip_count + fail_count != total_files:
        print(f"Warning: Count mismatch! ({upload_count}+{skip_count}+{fail_count} != {total_files})")
    
    return {
        'name': user_id_or_name,
        'total': total_files,
        'uploaded': upload_count,
        'skipped': skip_count,
        'failed': fail_count
    }

def upload_album(user_id_or_name, max_retries=3, local_check=False, block_size_mb=4, num_threads=1, cookies_path=None, config_files=None):
    targets = []
    
    # 1. Determine targets
    # Config Paths
    if config_files:
        all_configs = [(path, f"Custom Config {i+1}") for i, path in enumerate(config_files)]
    else:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        douyin_config = os.path.join(project_root, 'video_download_video_image', 'config', 'users.json')
        twitter_config = os.path.join(project_root, 'video_download_video_image', 'config', 'x_user.json')
        all_configs = [
            (douyin_config, "Douyin"),
            (twitter_config, "Twitter")
        ]

    def load_users(path, label):
        loaded_users = []
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    loaded_users = json.load(f)
                print(f"Loaded {len(loaded_users)} users from {label}")
            except Exception as e:
                print(f"Error reading {label} ({path}): {e}")
        else:
            print(f"Config not found: {label} ({path})")
        return loaded_users

    def add_to_targets(user_list):
        for user in user_list:
            u_id = user.get('id')
            name = user.get('name')
            t = u_id if u_id else name
            if t:
                targets.append({'target': t, 'display': f"{name}({u_id})" if name and u_id else t})
            else:
                print(f"Skipping invalid user entry: {user}")

    if user_id_or_name == 'all_twitter' and not config_files:
        # Backward compatibility for specific 'all_twitter' flag without explicit configs
        # Assuming if user passes config_files, they want 'all' to mean 'all in those files'
        # But 'all_twitter' is specific to internal logic.
        # If user provides custom configs, 'all_twitter' might be ambiguous unless they are twitter configs.
        # For simplicity, if config_files is set, we treat 'all' or 'all_xxx' as loading all from those files.
        # But let's keep the original specific flags if no custom config provided.
        print("--- Batch Mode: Twitter Users ---")
        add_to_targets(load_users(twitter_config, "Twitter Config"))
    elif user_id_or_name == 'all_douyin' and not config_files:
        print("--- Batch Mode: Douyin Users ---")
        add_to_targets(load_users(douyin_config, "Douyin Config"))
    elif user_id_or_name.startswith('all'):
        # Handles 'all', 'all_douyin' (with custom files), 'all_twitter' (with custom files)
        print("--- Batch Mode: All Users from Configs ---")
        for path, label in all_configs:
            add_to_targets(load_users(path, label))
    elif ',' in user_id_or_name:
        print(f"--- Batch Upload Mode: Processing specified list: {user_id_or_name} ---")
        raw_list = [x.strip() for x in user_id_or_name.split(',') if x.strip()]
        for t in raw_list:
            targets.append({'target': t, 'display': t})
    else:
        # Single user - Search in configs first
        found_in_config = False
        
        for cfg_path, cfg_name in all_configs:
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, 'r', encoding='utf-8') as f:
                        users = json.load(f)
                        for user in users:
                            # Check ID or Name
                            if str(user.get('id')) == str(user_id_or_name) or user.get('name') == user_id_or_name:
                                u_id = user.get('id')
                                name = user.get('name')
                                t = u_id if u_id else name
                                targets.append({'target': t, 'display': f"{name}({u_id})" if name and u_id else t})
                                found_in_config = True
                except Exception:
                    pass
        
        if not found_in_config:
            # Fallback to direct usage if not found in any config
            targets.append({'target': user_id_or_name, 'display': user_id_or_name})

    # 2. Process Targets
    summary_report = []
    total_targets = len(targets)
    
    if total_targets == 0:
        print("No targets to process.")
        return

    for i, item in enumerate(targets):
        target = item['target']
        display = item['display']
        
        print(f"\n[{i+1}/{total_targets}] Processing: {display}")
        stats = upload_task(target, max_retries, local_check, block_size_mb, num_threads, cookies_path, config_files)
        if stats:
            stats['user_display'] = display
            summary_report.append(stats)
            
    # 3. Final Summary (if more than 1 target or explicit batch mode)
    if len(targets) > 1 or user_id_or_name.startswith('all'):
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
        
        
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Upload images to a specified album.")
    parser.add_argument("--user_id_or_name", default="all_douyin", type=str, help="User ID or Name, or comma-separated list. Use 'all_twitter' or 'all_douyin' for batch. (default: all_douyin)")
    parser.add_argument("--retries", type=int, default=3, help="Max retries for failed uploads (default: 3)")
    parser.add_argument("--local_check", default=True, type=lambda x: (str(x).lower() == 'true'), help="Enable local history verification to skip uploaded files")
    parser.add_argument("--block_size", type=float, default=4, help="Upload block size in MB (default: 4)")
    parser.add_argument("--threads", type=int, default=1, help="Number of upload threads (default: 1)")
    parser.add_argument("--cookies_path", type=str, default='baiduphoto/cookies.json', help="Path to cookies.json (default: baiduphoto/cookies.json)")
    parser.add_argument("--config_files", type=str, nargs='+', default=None, help="List of user config JSON files. If not provided, defaults to internal project paths.")
     
    args = parser.parse_args()
    
    upload_album(args.user_id_or_name, args.retries, args.local_check, args.block_size, args.threads, args.cookies_path, args.config_files)
