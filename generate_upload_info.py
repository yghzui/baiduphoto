import json
import os
import sys

def generate_upload_info(query_id_or_name, config_files=None):
    """
    根据输入的id或者name，从配置的json中查找对应用户，
    并生成相册名和上传文件夹路径。
    
    Args:
        query_id_or_name (str): 用户的 id 或 name
        config_files (list, optional): 配置文件路径列表。如果不提供，使用默认配置。
        
    Returns:
        tuple: (album_name, upload_dir) 如果找到，否则返回 (None, None)
    """
    
    # Determine config paths
    if config_files:
        config_paths = config_files
    else:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        douyin_config = os.path.join(project_root, 'video_download_video_image', 'config', 'users.json')
        twitter_config = os.path.join(project_root, 'video_download_video_image', 'config', 'x_user.json')
        config_paths = [douyin_config, twitter_config]

    users = []
    
    for config_path in config_paths:
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        users.extend(data)
                    else:
                         print(f"Warning: {config_path} does not contain a list of users.")
            except Exception as e:
                print(f"Error reading config {config_path}: {e}")
        else:
             # Only warn if it was explicitly passed, otherwise silent skip for defaults
             if config_files:
                 print(f"Warning: Config file not found: {config_path}")

    found_user = None
    for user in users:
        # Check id or name
        if str(user.get('id')) == str(query_id_or_name) or user.get('name') == query_id_or_name:
            found_user = user
            break
    
    if not found_user:
        print(f"User with id or name '{query_id_or_name}' not found.")
        return None, None

    # 获取所需字段
    name = found_user.get('name', '')
    user_id = found_user.get('id', '')
    save_dir = found_user.get('save_dir', '')
    archive_by_author_id = found_user.get('archive_by_author_id', 0)

    # 1. 生成相册名: name_id
    album_name = f"{name}_{user_id}"

    # 2. 生成上传文件夹路径
    # 如果 archive_by_author_id 为 1，路径为 save_dir/id
    # 否则为 save_dir
    if archive_by_author_id == 1:
        upload_dir = os.path.join(save_dir, user_id)
    else:
        upload_dir = save_dir

    # 打印结果
    print(f"相册名 (Album Name): {album_name}")
    print(f"上传文件夹 (Upload Dir): {upload_dir}")

    return album_name, upload_dir

if __name__ == "__main__":
    # 测试代码
    if len(sys.argv) > 1:
        input_query = sys.argv[1]
        generate_upload_info(input_query)
    else:
        # 默认测试用例 (可以使用文件中存在的 id 或 name)
        print("--- Test Case 1: id '520yuanmengqi' ---")
        generate_upload_info('520yuanmengqi')
        
        print("\n--- Test Case 2: name '我的猫不爱我' ---")
        generate_upload_info('我的猫不爱我')
