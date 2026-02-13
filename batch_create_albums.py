import json
import os
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from pybaiduphoto import API

def batch_create_albums():
    print("--- Starting batch album creation ---")

    # 1. Load Cookies and Init API
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

    # 2. Load users.json
    users_path = os.path.join(os.getcwd(), 'users.json')
    if not os.path.exists(users_path):
        print(f"Error: users.json not found at {users_path}")
        return

    try:
        with open(users_path, 'r', encoding='utf-8') as f:
            users = json.load(f)
        print(f"Loaded {len(users)} users from users.json")
    except Exception as e:
        print(f"Error reading users.json: {e}")
        return

    # 3. Fetch existing albums
    print("Fetching existing albums list...")
    try:
        existing_albums = api.get_self_All(typeName='Album')
        existing_album_names = set()
        if existing_albums:
            for album in existing_albums:
                try:
                    existing_album_names.add(album.getName())
                except:
                    pass
        print(f"Found {len(existing_album_names)} existing albums.")
    except Exception as e:
        print(f"Error fetching existing albums: {e}")
        return

    # 4. Iterate and Create
    created_count = 0
    skipped_count = 0
    failed_count = 0

    print("\nProcessing users...")
    for i, user in enumerate(users):
        name = user.get('name')
        user_id = user.get('id')
        
        if not name or not user_id:
            print(f"[{i+1}/{len(users)}] Skipping invalid user entry: {user}")
            continue

        album_name = f"{name}_{user_id}"
        
        if album_name in existing_album_names:
            print(f"[{i+1}/{len(users)}] [Skip] Album exists: {album_name}")
            skipped_count += 1
        else:
            print(f"[{i+1}/{len(users)}] [Create] Creating: {album_name} ...")
            try:
                api.createNewAlbum(Name=album_name)
                existing_album_names.add(album_name) 
                created_count += 1
                print("    -> Success")
            except Exception as e:
                print(f"    -> Failed: {e}")
                failed_count += 1

    print("\n--- Batch Creation Summary ---")
    print(f"Total Users: {len(users)}")
    print(f"Created: {created_count}")
    print(f"Skipped: {skipped_count}")
    print(f"Failed:  {failed_count}")

if __name__ == '__main__':
    batch_create_albums()
