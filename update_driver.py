import os
import requests
import zipfile
import shutil
import subprocess
import winreg

def get_chrome_version():
    """获取本机已安装的 Chrome 版本号"""
    # 方法1：从注册表读取
    reg_paths = [
        (winreg.HKEY_CURRENT_USER,  r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon"),
    ]
    for hive, path in reg_paths:
        try:
            key = winreg.OpenKey(hive, path)
            version, _ = winreg.QueryValueEx(key, "version")
            winreg.CloseKey(key)
            return version
        except Exception:
            pass

    # 方法2：从 chrome.exe 文件属性读取
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(chrome_path):
        chrome_path = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    if os.path.exists(chrome_path):
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 f"(Get-Item '{chrome_path}').VersionInfo.FileVersion"],
                capture_output=True, text=True, timeout=10
            )
            ver = result.stdout.strip()
            if ver:
                return ver
        except Exception:
            pass

    return None

def find_matching_chromedriver_url(chrome_major):
    """从 Chrome for Testing API 中找到与 Chrome 主版本号匹配的 ChromeDriver 下载链接"""
    print(f"正在查找匹配 Chrome {chrome_major} 的 ChromeDriver 版本...")
    try:
        resp = requests.get(
            "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json",
            timeout=15
        )
        data = resp.json()
        # 倒序遍历，找第一个主版本匹配的条目
        for entry in reversed(data.get("versions", [])):
            ver = entry.get("version", "")
            if ver.startswith(f"{chrome_major}.") and "chromedriver" in entry.get("downloads", {}):
                for item in entry["downloads"]["chromedriver"]:
                    if item["platform"] == "win64":
                        print(f"找到匹配版本: {ver}")
                        return ver, item["url"]
    except Exception as e:
        print(f"查询匹配版本失败: {e}")
    return None, None

def update_chromedriver():
    """自动下载并更新与本机 Chrome 版本匹配的 ChromeDriver"""

    # 第一步：检测本机 Chrome 版本
    chrome_version = get_chrome_version()
    if chrome_version:
        chrome_major = chrome_version.split(".")[0]
        print(f"检测到本机 Chrome 版本: {chrome_version}（主版本号: {chrome_major}）")
    else:
        print("无法检测本机 Chrome 版本，将下载最新 Stable 版 ChromeDriver")
        chrome_major = None

    # 第二步：获取对应版本的下载链接
    print("正在获取 ChromeDriver 版本信息...")
    stable_version, download_url = None, None

    if chrome_major:
        stable_version, download_url = find_matching_chromedriver_url(chrome_major)

    if not download_url:
        # 回退：下载 Stable 渠道的最新版本
        print("回退到 Stable 渠道最新版本...")
        try:
            response = requests.get(
                "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json",
                timeout=15
            )
            data = response.json()
            stable_version = data['channels']['Stable']['version']
            downloads = data['channels']['Stable']['downloads']['chromedriver']
            for item in downloads:
                if item['platform'] == 'win64':
                    download_url = item['url']
                    break
        except Exception as e:
            print(f"获取版本信息失败: {e}")
            return False

    if not download_url:
        print("未找到可用的 ChromeDriver 下载地址")
        return False

    print(f"目标 ChromeDriver 版本: {stable_version}")
    print(f"下载地址: {download_url}")
    
    # 下载ChromeDriver
    print("正在下载ChromeDriver...")
    try:
        response = requests.get(download_url, stream=True)
        response.raise_for_status()
        
        # 保存到临时文件
        temp_zip = "chromedriver_temp.zip"
        with open(temp_zip, 'wb') as f:
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(f"\r下载进度: {percent:.1f}%", end='')
        
        print("\n下载完成!")
        
        # 解压文件
        print("正在解压文件...")
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            zip_ref.extractall("temp_driver")
        
        # 查找chromedriver.exe
        chromedriver_path = None
        for root, dirs, files in os.walk("temp_driver"):
            for file in files:
                if file == "chromedriver.exe":
                    chromedriver_path = os.path.join(root, file)
                    break
            if chromedriver_path:
                break
        
        if not chromedriver_path:
            print("解压后未找到chromedriver.exe")
            return False
        
        # 备份旧的驱动
        drivers_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'drivers')
        old_driver = os.path.join(drivers_dir, 'chromedriver.exe')
        
        if os.path.exists(old_driver):
            backup_path = os.path.join(drivers_dir, 'chromedriver_old.exe')
            print(f"正在备份旧驱动到: {backup_path}")
            shutil.copy2(old_driver, backup_path)
        
        # 复制新驱动
        print("正在替换驱动文件...")
        shutil.copy2(chromedriver_path, old_driver)
        
        # 清理临时文件
        print("正在清理临时文件...")
        os.remove(temp_zip)
        shutil.rmtree("temp_driver")
        
        print(f"\n✓ ChromeDriver已成功更新到版本 {stable_version}!")
        return True
        
    except Exception as e:
        print(f"\n下载或安装失败: {e}")
        # 清理临时文件
        try:
            if os.path.exists("chromedriver_temp.zip"):
                os.remove("chromedriver_temp.zip")
            if os.path.exists("temp_driver"):
                shutil.rmtree("temp_driver")
        except:
            pass
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("ChromeDriver自动更新工具")
    print("=" * 60)
    update_chromedriver()
    input("\n按Enter键退出...")
