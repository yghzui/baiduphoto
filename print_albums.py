import json
import os
import sys

# Add current directory to path to ensure pybaiduphoto can be imported
sys.path.append(os.getcwd())

from pybaiduphoto import API

def main():
    # Load cookies
    cookie_path = os.path.join(os.getcwd(), 'cookies.json')
    if not os.path.exists(cookie_path):
        print(f"Error: {cookie_path} not found.")
        return

    try:
        with open(cookie_path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
    except Exception as e:
        print(f"Error reading cookies.json: {e}")
        return

    # Initialize API
    print("Initializing API...")
    try:
        api = API(cookies=cookies)
    except Exception as e:
        print(f"Error initializing API: {e}")
        return

    # Get all albums
    print("Fetching albums...")
    try:
        # Using get_self_All to get all albums
        albums = api.get_self_All(typeName='Album')
        
        print(f"Found {len(albums)} albums:")
        
        for i, album in enumerate(albums):
            try:
                name = album.getName()
                print(f"{i+1}. {name}")
            except Exception as e:
                print(f"{i+1}. Error getting name: {e}")
                
    except Exception as e:
        print(f"Error fetching albums: {e}")

if __name__ == '__main__':
    main()
