import os
import json
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from pybaiduphoto import API

def inspect_album_info():
    # 1. Login
    cookie_path = os.path.join(os.getcwd(), 'cookies.json')
    if not os.path.exists(cookie_path):
        print(f"Error: cookies.json not found at {cookie_path}")
        return
        
    try:
        with open(cookie_path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        api = API(cookies=cookies)
        print("API initialized successfully.\n")
    except Exception as e:
        print(f"Login failed: {e}")
        return

    # 2. Get the first album and inspect its info
    try:
        albums = api.get_self_All('Album', max=1)
        if not albums:
            print("No albums found.")
            return

        album = albums[0]
        print(f"--- Inspecting Album: {album.getName()} (ID: {album.getID()}) ---")
        
        # Access the protected/private info attribute directly for inspection
        info = album.info
        print(json.dumps(info, indent=4, ensure_ascii=False))

    except Exception as e:
        print(f"Error fetching albums: {e}")

if __name__ == '__main__':
    inspect_album_info()
