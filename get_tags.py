import os
import json
import sys

# Add current directory to path
sys.path.append(os.getcwd())

from pybaiduphoto import API

def get_tags():
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

    # 2. Get Person Tags
    print("--- Fetching Person Tags ---")
    try:
        persons = api.get_self_All('Person')
        print(f"Found {len(persons)} person tags.")
        for p in persons[:5]: # Show first 5
            print(f"  - {p.getName()} (ID: {p.getID()})")
        if len(persons) > 5:
            print(f"  ... and {len(persons)-5} more.")
    except Exception as e:
        print(f"Error fetching Person tags: {e}")
    print()

    # 3. Get Thing Tags
    print("--- Fetching Thing Tags ---")
    try:
        things = api.get_self_All('Thing')
        print(f"Found {len(things)} thing tags.")
        for t in things[:5]:
            print(f"  - {t.getName()} (ID: {t.getID()})")
        if len(things) > 5:
            print(f"  ... and {len(things)-5} more.")
    except Exception as e:
        print(f"Error fetching Thing tags: {e}")
    print()

    # 4. Get Location Tags
    print("--- Fetching Location Tags ---")
    try:
        locations = api.get_self_All('Location')
        print(f"Found {len(locations)} location tags.")
        for l in locations[:5]:
            print(f"  - {l.getName()} (ID: {l.getID()})")
        if len(locations) > 5:
            print(f"  ... and {len(locations)-5} more.")
    except Exception as e:
        print(f"Error fetching Location tags: {e}")

if __name__ == '__main__':
    get_tags()
