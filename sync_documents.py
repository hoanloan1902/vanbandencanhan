import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import requests

# Lấy cấu hình bảo mật từ biến môi trường GitHub Secrets
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Nếu anh có dùng chat id cá nhân hoặc group id, có thể đưa vào secret luôn:
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "") 

def send_telegram_message(message):
    """Hàm gửi thông báo qua Telegram Bot"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Chưa cấu hình Telegram Token hoặc Chat ID.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[+] Gửi thông báo Telegram thành công!")
        else:
            print(f"[!] Lỗi gửi Telegram: {response.text}")
    except Exception as e:
        print(f"[!] Không thể kết nối tới Telegram API: {e}")

def main():
    print("1. Đang khởi động Chrome...")
    options = Options()
    options.add_argument("--headless")  # Chạy ngầm trên GitHub Actions
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    
    # Quan trọng: Tối ưu chống lỗi Timeout từ renderer khi mạng chậm
    options.page_load_strategy = 'eager'

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(120) # Tăng timeout lên 120 giây
    driver.set_script_timeout(120)

    try:
        print("2. Đang truy cập hệ thống quản lý văn bản...")
        driver.get("https://hscvkhcn.dienbien.gov.vn")
        
        # Thêm đoạn logic đăng nhập, quét văn bản và đồng bộ Google Sheets của anh ở đây...
        # Ví dụ chờ phần tử trang load xong:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        print("[+] Đã truy cập và kết nối thành công tới hệ thống HSCV.")
        
        # Sau khi xử lý quét văn bản xong, anh có thể gọi hàm thông báo:
        # send_telegram_message("✅ Đã quét và cập nhật danh sách văn bản đến thành công!")

    except Exception as e:
        error_msg = f"[!] Lỗi trong quá trình chạy script: {str(e)}"
        print(error_msg)
        send_telegram_message(error_msg)
        raise e
    finally:
        driver.quit()
        print("3. Đã đóng trình duyệt.")

if __name__ == "__main__":
    main()
