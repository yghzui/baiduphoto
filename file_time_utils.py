import os
import re
import subprocess
import json
from datetime import datetime, timedelta
from PIL import Image

def get_video_creation_time(file_path):
    """
    尝试使用 ffprobe 读取视频的 creation_time
    """
    try:
        cmd = [
            'ffprobe', 
            '-v', 'quiet', 
            '-print_format', 'json', 
            '-show_format', 
            '-show_streams', 
            file_path
        ]
        # 使用 subprocess.run 执行命令
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode != 0:
            return None
            
        data = json.loads(result.stdout)
        
        # 尝试从 format tags 中读取
        creation_time_str = None
        if 'format' in data and 'tags' in data['format']:
            tags = data['format']['tags']
            # 常见标签名
            for key in ['creation_time', 'date', 'com.apple.quicktime.creationdate']:
                if key in tags:
                    creation_time_str = tags[key]
                    break
        
        # 如果 format 中没有，尝试从 streams 中读取 (通常是 video stream)
        if not creation_time_str and 'streams' in data:
            for stream in data['streams']:
                if 'tags' in stream:
                    if 'creation_time' in stream['tags']:
                        creation_time_str = stream['tags']['creation_time']
                        break
        
        if creation_time_str:
            # 解析时间字符串
            # 常见格式: 2025-01-01T12:00:00.000000Z
            try:
                # 处理带有时区的情况 (简单处理 Z 为 UTC)
                if creation_time_str.endswith('Z'):
                    dt = datetime.strptime(creation_time_str, '%Y-%m-%dT%H:%M:%S.%fZ')
                    # 转换为本地时间 (假设用户在中国，+8)
                    # 或者简单地，如果文件名没有时间，这个时间通常比上传时间更准确
                    # 这里简单加上8小时转为北京时间
                    dt = dt + timedelta(hours=8)
                    return dt.timestamp()
                elif '+' in creation_time_str: # ISO format with offset
                     # 简化处理，尝试直接解析
                     dt = datetime.fromisoformat(creation_time_str)
                     return dt.timestamp()
                else:
                    # 尝试其他格式
                    for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y:%m:%d %H:%M:%S']:
                        try:
                            dt = datetime.strptime(creation_time_str, fmt)
                            return dt.timestamp()
                        except ValueError:
                            continue
            except Exception as e:
                print(f"Debug: Failed to parse video time {creation_time_str}: {e}")
                
    except Exception as e:
        print(f"Debug: FFprobe failed for {file_path}: {e}")
        
    return None

def try_fix_file_time(file_path):
    """
    尝试从文件名或EXIF中提取拍摄时间，并修改文件的mtime/atime。
    如果成功提取到时间，返回 True，否则返回 False。
    此函数用于解决上传到百度网盘时时间变为上传时间的问题。
    """
    try:
        taken_time = None
        filename = os.path.basename(file_path)
        
        # 1. 尝试从文件名解析 (优先，因为下载的文件名通常包含准确时间)
        # 常见格式: 
        # 2025-01-01 12-00-00 (Twitter/Douyin Custom)
        # 20250101_120000 (Standard)
        # 2025-01-01_12-00-00
        # IMG_20250101_120000
        patterns = [
            (r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2})-(\d{2})-(\d{2})', '%Y-%m-%d %H-%M-%S'), # 2025-01-01 12-00-00
            (r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', '%Y%m%d_%H%M%S'),           # 20250101_120000
            (r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})', '%Y-%m-%d_%H-%M-%S'),   # 2025-01-01_12-00-00
        ]
        
        for pat, fmt in patterns:
            match = re.search(pat, filename)
            if match:
                try:
                    if len(match.groups()) == 6:
                        dt = datetime(
                            int(match.group(1)), int(match.group(2)), int(match.group(3)),
                            int(match.group(4)), int(match.group(5)), int(match.group(6))
                        )
                        taken_time = dt.timestamp()
                        # print(f"DEBUG: Parsed time from filename: {dt} for {filename}")
                        break
                except ValueError:
                    continue
        
        # 2. 如果文件名没有时间，尝试读取 EXIF (图片) 或 Metadata (视频)
        if taken_time is None:
            lower_name = filename.lower()
            if lower_name.endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic', '.tif', '.tiff')):
                try:
                    # 仅读取头部信息，避免加载整个大图
                    with Image.open(file_path) as img:
                        exif_data = img._getexif()
                        if exif_data:
                            # 36867: DateTimeOriginal, 36868: DateTimeDigitized, 306: DateTime
                            for tag_id in [36867, 36868, 306]:
                                if tag_id in exif_data:
                                    date_str = exif_data[tag_id]
                                    # 格式通常是 'YYYY:MM:DD HH:MM:SS'
                                    try:
                                        dt = datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
                                        taken_time = dt.timestamp()
                                        # print(f"DEBUG: Parsed time from EXIF: {dt} for {filename}")
                                        break
                                    except ValueError:
                                        continue
                except Exception:
                    pass
            elif lower_name.endswith(('.mp4', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.m4v', '.ts', '.webm', '.vob', '.mts', '.m2ts')):
                 taken_time = get_video_creation_time(file_path)

        # 3. 应用时间修改
        if taken_time:
            # 总是修改为拍摄时间，确保 mtime 是最早的
            # Windows 上 os.utime 修改的是 atime 和 mtime
            os.utime(file_path, (taken_time, taken_time))
            return True
            
    except Exception as e:
        print(f"Warning: Failed to fix time for {file_path}: {e}")
    
    return False
