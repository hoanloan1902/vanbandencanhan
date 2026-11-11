import builtins
import os
import re
import sys
import warnings
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlparse
from datetime import datetime

import requests
import urllib3
from pypdf import PdfReader
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Ép Python in log ngay lập tức theo thời gian thực trên GitHub Actions
def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    builtins.print(*args, **kwargs)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

try:
    import google.generativeai as genai
except ImportError:
    genai = None

# ==========================================
# CẤU HÌNH HỆ THỐNG & TÍCH HỢP
# ==========================================
HOST = "hscvkhcn.dienbien.gov.vn"
URL_LOGIN = f"https://{HOST}/qlvb/index.nsf"
USERNAME = "hoanph.skhcn"

# Thông tin Telegram Bot của anh
TELEGRAM_BOT_TOKEN = "9155938875:AAGMZrCa-57ZvJvrJYI5YzPK-uVmcEr0EsA"
TELEGRAM_CHAT_ID = "7113441083"

# Thư mục lưu trữ tạm thời trên GitHub Actions
DRIVE_ROOT = Path("VanBanCoHan")
MAX_BYTES = 30 * 1024 * 1024

def tao_trinh_duyet():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1366,900")
    options.accept_insecure_certs = True
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    return driver

def dang_nhap(driver):
    password = os.getenv("OFFICE_PASSWORD", "")
    if not USERNAME or not password:
        raise RuntimeError("Thiếu thông tin đăng nhập (chưa cấu hình OFFICE_PASSWORD trong GitHub Secrets).")

    wait = WebDriverWait(driver, 40)
    driver.get(URL_LOGIN)

    user_input = wait.until(EC.visibility_of_element_located((By.NAME, "Username")))
    pass_input = wait.until(EC.visibility_of_element_located((By.NAME, "Password")))

    user_input.clear()
    user_input.send_keys(USERNAME)
    pass_input.clear()
    pass_input.send_keys(password)

    form = pass_input.find_element(By.XPATH, "./ancestor::form[1]")
    form.find_element(By.CSS_SELECTOR, "input[type='submit']").click()
    wait.until(EC.invisibility_of_element_located((By.NAME, "Password")))
    print("-> Đăng nhập thành công vào hệ thống HSCV!")

def thu_thập_link_van_ban(driver):
    """Quét đệ quy toàn bộ khung (iframe/frame) để tìm chuẩn xác các link văn bản đến cá nhân"""
    doc_urls = []
    
    def quet_khung(drv, depth=0):
        if depth > 4: 
            return
        # Quét các thẻ a dẫn đến trang chi tiết văn bản đến
        for link in drv.find_elements(By.XPATH, "//a[contains(@href, '/vbden.nsf/str/')]"):
            try:
                href = link.get_attribute("href") or ""
                parsed = urlparse(href)
                path = parsed.path.casefold()
                
                is_doc_link = (
                    parsed.hostname == HOST and
                    any(sub in path for sub in ["/vbden.nsf/str/", "/vbden.nsf/0/", "opendocument"]) and
                    "openview" not in path and
                    "openform" not in path and
                    "default" not in path and
                    "login" not in path and "logout" not in path
                )
                
                if is_doc_link and href not in doc_urls:
                    doc_urls.append(href)
            except Exception:
                continue
                
        # Tiếp tục chui vào các frame/iframe con bên trong
        for f in drv.find_elements(By.CSS_SELECTOR, "frame, iframe"):
            try:
                drv.switch_to.frame(f)
                quet_khung(drv, depth + 1)
                drv.switch_to.parent_frame()
            except Exception:
                drv.switch_to.default_content()

    driver.switch_to.default_content()
    quet_khung(driver)
    return doc_urls

def chuyen_trang_2(driver):
    def click_next(drv):
        try:
            elements = drv.find_elements(By.XPATH, "//*[contains(text(), 'Trang 2') or contains(@title, 'Trang sau') or contains(text(), '>') or contains(@class, 'next')]")
            for el in elements:
                if el.is_displayed():
                    drv.execute_script("arguments[0].click();", el)
                    return True
        except Exception:
            pass
        
        for f in drv.find_elements(By.CSS_SELECTOR, "frame, iframe"):
            try:
                drv.switch_to.frame(f)
                if click_next(drv):
                    return True
                drv.switch_to.parent_frame()
            except Exception:
                drv.switch_to.default_content()
        return False
    return click_next(driver)

def tim_file_pdf(driver):
    files = {}
    for link in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
        href = link.get_attribute("href") or ""
        parsed = urlparse(href)
        if parsed.scheme != "https" or parsed.hostname != HOST:
            continue
        path = unquote(parsed.path)
        if "/$file/" not in path.casefold():
            continue
        filename = path.rsplit("/", 1)[-1]
        if filename.casefold().startswith("dongdau") and filename.casefold().endswith(".pdf"):
            files[href] = filename
    return files

def tai_pdf(driver, pdf_url, detail_url):
    with requests.Session() as session:
        session.headers.update({
            "User-Agent": driver.execute_script("return navigator.userAgent"),
            "Referer": detail_url,
        })
        for cookie in driver.get_cookies():
            domain = cookie.get("domain", HOST)
            if HOST in domain:
                session.cookies.set(cookie["name"], cookie["value"], domain=domain, path=cookie.get("path", "/"))
        
        response = session.get(pdf_url, stream=True, verify=False, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        
        data = response.content
        if b"%PDF-" not in data[:1024]:
            raise RuntimeError("Dữ liệu không phải định dạng PDF.")
        return data

def kiem_tra_va_trich_xuat_chu(pdf_data):
    reader = PdfReader(BytesIO(pdf_data))
    if reader.is_encrypted:
        return ""
    return "".join((page.extract_text() or "") + "\n" for page in reader.pages)

def trich_xuat_han_xu_ly(text):
    match = re.search(r"(?i)(?:trước ngày|chậm nhất ngày|hoàn thành trước ngày|thời hạn báo cáo)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text)
    return match.group(1) if match else None

def tom_tat_bang_gemini(text):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not genai:
        return "Không có AI tóm tắt."
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        res = model.generate_content(f"Đóng vai chuyên viên hành chính, tóm tắt ngắn gọn: 1. Yêu cầu chính? 2. Đơn vị chủ trì? 3. Hạn xử lý?\n\nNội dung văn bản:\n{text[:10000]}")
        return res.text
    except Exception as e:
        return f"Lỗi gọi Gemini API: {e}"

def gui_telegram(han_xu_ly, summary_text, file_path):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url_msg, data={"chat_id": TELEGRAM_CHAT_ID, "text": f"🚨 *CẢNH BÁO VĂN BẢN CÓ HẠN*\n⏱ *Hạn xử lý:* {han_xu_ly}\n\n🤖 *Tóm tắt AI:*\n{summary_text}", "parse_mode": "Markdown"}, verify=False, timeout=15)
        
        url_doc = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        with open(file_path, "rb") as f:
            requests.post(url_doc, data={"chat_id": TELEGRAM_CHAT_ID}, files={"document": f}, verify=False, timeout=30)
        print("-> [Telegram] Đã gửi thông tin và văn bản gốc thành công!")
    except Exception as e:
        print(f"[!] Lỗi gửi Telegram: {e}")

def main():
    driver = None
    try:
        print("1. Đang khởi động Chrome (Headless)...")
        driver = tao_trinh_duyet()

        print("2. Đang đăng nhập hệ thống văn bản...")
        dang_nhap(driver)
        
        print("3. Đang truy cập chính xác danh sách Xử lý văn bản cá nhân...")
        url_view = f"https://{HOST}/qlvb/vbden.nsf/default?openform&frm=Private_ChoXL?openForm"
        driver.get(url_view)
        
        print("4. Đang chờ khung dữ liệu tải và quét link...")
        import time; time.sleep(8) # Chờ frameset load hoàn toàn
        
        all_doc_urls = thu_thập_link_van_ban(driver)
        print(f"-> Tìm thấy {len(all_doc_urls)} văn bản ở Trang 1.")

        if chuyen_trang_2(driver):
            print("-> Đang chuyển sang Trang 2...")
            time.sleep(4)
            doc_urls_trang2 = thu_thập_link_van_ban(driver)
            print(f"-> Tìm thấy {len(doc_urls_trang2)} văn bản ở Trang 2.")
            for u in doc_urls_trang2:
                if u not in all_doc_urls: 
                    all_doc_urls.append(u)

        print(f"==> Tổng số văn bản cần kiểm tra: {len(all_doc_urls)}")

        if len(all_doc_urls) == 0:
            print("[!] Cảnh báo: Không quét được văn bản nào. Kiểm tra lại khung giao diện.")
            return 1

        DRIVE_ROOT.mkdir(parents=True, exist_ok=True)
        danh_sach_tong_hop = []

        for idx, url_detail in enumerate(all_doc_urls, start=1):
            print(f"\n--- Xử lý văn bản {idx}/{len(all_doc_urls)} ---")
            try:
                driver.get(url_detail)
                WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                
                files = tim_file_pdf(driver)
                if len(files) == 0:
                    print(" -> Không có file PDF đóng dấu chuẩn. Ghi nhận văn bản thường.")
                    danh_sach_tong_hop.append({
                        "filename": f"Văn bản {idx} (Không có PDF)",
                        "han": "Không có hạn",
                        "summary": "Văn bản không có tệp đính kèm đóng dấu."
                    })
                    continue

                pdf_url, filename = next(iter(files.items()))
                print(f" -> Đang tải tệp: {filename}...")
                
                pdf_data = tai_pdf(driver, pdf_url, url_detail)
                filepath = DRIVE_ROOT / filename
                filepath.write_bytes(pdf_data)
                
                text_van_ban = kiem_tra_va_trich_xuat_chu(pdf_data)
                han_xu_ly = trich_xuat_han_xu_ly(text_van_ban) or "Không có hạn"
                
                if han_xu_ly != "Không có hạn":
                    print(f" -> [PHÁT HIỆN CÓ HẠN]: {han_xu_ly} -> Đang gọi AI tóm tắt...")
                    summary_text = tom_tat_bang_gemini(text_van_ban)
                    gui_telegram(han_xu_ly, summary_text, filepath)
                else:
                    print(" -> Văn bản thông thường, không có hạn cụ thể.")
                    summary_text = "Văn bản thông thường."

                danh_sach_tong_hop.append({
                    "filename": filename,
                    "han": han_xu_ly,
                    "summary": summary_text
                })

            except Exception as e:
                print(f" -> Lỗi khi xử lý văn bản này: {e}")

        print(f"\n=> Quét xong toàn bộ {len(danh_sach_tong_hop)} văn bản.")
        print("=== HOÀN TẤT LUỒNG VĂN BẢN ĐẾN! ===")
        return 0

    except Exception as error:
        print(f"\n[!] LỖI HỆ THỐNG: {error}")
        return 1
    finally:
        if driver: 
            driver.quit()

if __name__ == "__main__":
    sys.exit(main())
