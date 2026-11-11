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
    import gspread
except ImportError:
    gspread = None

# ==========================================
# CẤU HÌNH HỆ THỐNG & GOOGLE SHEETS
# ==========================================
HOST = "hscvkhcn.dienbien.gov.vn"
URL_LOGIN = f"https://{HOST}/qlvb/index.nsf"
USERNAME = "hoanph.skhcn"

# Cấu hình Google Sheets từ GitHub Secrets
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_WORKSHEET = os.getenv("GOOGLE_WORKSHEET", "VB_Den")
CREDENTIALS_FILE = "credentials/service-account.json"

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
        raise RuntimeError("Thiếu thông tin đăng nhập (OFFICE_PASSWORD).")

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

def thu_thập_thong_tin_trang_hien_tai(driver):
    danh_sach = []
    
    def quet_khung(drv, depth=0):
        if depth > 4: 
            return
        
        rows = drv.find_elements(By.TAG_NAME, "tr")
        for row in rows:
            try:
                checkboxes = row.find_elements(By.XPATH, ".//input[@type='checkbox']")
                if not checkboxes:
                    continue
                
                row_text = row.text.strip()
                if not row_text:
                    continue
                
                links = row.find_elements(By.XPATH, ".//a[contains(@href, '/vbden.nsf/str/') or contains(@href, '/vbden.nsf/0/') or contains(@href, 'OpenDocument')]")
                url_detail = ""
                for link in links:
                    href = link.get_attribute("href") or ""
                    parsed = urlparse(href)
                    path = parsed.path.casefold()
                    if parsed.hostname == HOST and any(sub in path for sub in ["/vbden.nsf/str/", "/vbden.nsf/0/", "opendocument"]):
                        url_detail = href
                        break
                
                if url_detail:
                    danh_sach.append({
                        "row_text": row_text,
                        "url": url_detail
                    })
            except Exception:
                continue
                
        for f in drv.find_elements(By.CSS_SELECTOR, "frame, iframe"):
            try:
                drv.switch_to.frame(f)
                quet_khung(drv, depth + 1)
                drv.switch_to.parent_frame()
            except Exception:
                drv.switch_to.default_content()

    driver.switch_to.default_content()
    quet_khung(driver)
    return danh_sach

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
        if filename.casefold().endswith(".pdf"):
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
        return response.content

def kiem_tra_va_trich_xuat_chu(pdf_data):
    try:
        reader = PdfReader(BytesIO(pdf_data))
        if reader.is_encrypted:
            return ""
        text = ""
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
        return text
    except Exception:
        return ""

def trich_xuat_thong_tin_chi_tiet(row_text, pdf_text):
    """
    Bóc tách Số hiệu, Trích yếu và quy tắc hạn xử lý:
    - Nếu có mốc thời gian và (giao riêng Sở KH&CN HOẶC giao chung các sở ban ngành) -> Có hạn.
    - Chỉ giao riêng cho sở/ngành khác (không dính dáng gì đến Sở KH&CN) -> Không có hạn.
    """
    so_hieu = "Không xác định"
    trich_yeu = row_text[:100]
    han_xu_ly = "Không có hạn"

    # 1. Bóc tách số hiệu
    match_so = re.search(r'([A-Za-z0-9/-]+(?:UBND|STC|SKHCN|VPCP)[A-Za-z0-9/-]*)', row_text)
    if match_so:
        so_hieu = match_so.group(1)

    # 2. Phân tích nội dung văn bản
    combined_text = row_text + "\n" + pdf_text
    
    # Kiểm tra đối tượng nhận/chủ trì
    lien_quan_skhcn = bool(re.search(r"(?i)(?:sở khoa học và công nghệ|skhcn)", combined_text))
    giao_chung_cac_so = bool(re.search(r"(?i)(?:các sở, ban, ngành|các sở, ban, ngành, đoàn thể|các cơ quan, đơn vị|UBND các huyện, thị xã, thành phố)", combined_text))
    
    # Kiểm tra xem có giao riêng cho một sở khác cụ thể nào không (ví dụ: Sở GTVT, Sở Y Tế...) mà không có tên Sở KH&CN
    giao_rieng_so_khac = bool(re.search(r"(?i)sở\s+(?:giao thông vận tải|y tế|giáo dục và đào tạo|tài chính|tài nguyên và môi trường|nông nghiệp và phát triển nông thôn|lao động)", combined_text))

    # Tìm mốc thời hạn trong văn bản
    match_han = re.search(r"(?i)(?:trước ngày|chậm nhất ngày|hoàn thành trước ngày|thời hạn báo cáo|thực hiện trước)\s*[:]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", combined_text)
    
    if match_han:
        ngay_tim_thay = match_han.group(1)
        
        # Điều kiện để tính là "Có hạn":
        # - Hoặc là có nhắc đến Sở KH&CN
        # - Hoặc là giao chung chung cho các sở ban ngành
        # - Miễn là KHÔNG phải chỉ giao riêng cho một sở khác độc lập
        if lien_quan_skhcn or giao_chung_cac_so or not giao_rieng_so_khac:
            han_xu_ly = f"Trước ngày {ngay_tim_thay}"

    return so_hieu, trich_yeu, han_xu_ly

def cap_nhat_google_sheets(danh_sach_ket_qua):
    if not Path(CREDENTIALS_FILE).exists() or not GOOGLE_SHEET_ID or not gspread:
        print("[!] Thiếu cấu hình Google Sheets.")
        return

    try:
        client = gspread.service_account(filename=CREDENTIALS_FILE)
        book = client.open_by_key(GOOGLE_SHEET_ID)
        try:
            sheet = book.worksheet(GOOGLE_WORKSHEET)
        except gspread.WorksheetNotFound:
            sheet = book.add_worksheet(title=GOOGLE_WORKSHEET, rows=1000, cols=10)
        
        sheet.clear()
        sheet.append_row(["STT", "Số hiệu văn bản", "trích yếu", "hạn xử lý", "Trạng thái", "Lần kiểm tra gần nhất"])
        
        thoi_gian_hien_tai = datetime.now().strftime("%d/11/2026 %H:%M:%S")
        for idx, item in enumerate(danh_sach_ket_qua, start=1):
            sheet.append_row([
                idx,
                item["so_hieu"],
                item["trich_yeu"],
                item["han_xu_ly"],
                "Chờ xử lý",
                thoi_gian_hien_tai
            ])
        print("-> [Google Sheets] Đã đồng bộ thành công!")
    except Exception as e:
        print(f"[!] Lỗi đồng bộ Google Sheets: {e}")

def main():
    driver = None
    try:
        print("1. Đang khởi động Chrome (Headless)...")
        driver = tao_trinh_duyet()

        print("2. Đang đăng nhập hệ thống văn bản...")
        dang_nhap(driver)
        
        print("3. Đang truy cập danh sách 'Xử lý văn bản cá nhân'...")
        url_view = f"https://{HOST}/qlvb/vbden.nsf/default?openform&frm=Private_ChoXL?openForm"
        driver.get(url_view)
        
        print("4. Đang chờ tải dữ liệu trang 1...")
        import time; time.sleep(6)
        
        tat_ca_van_ban = thu_thập_thong_tin_trang_hien_tai(driver)
        print(f"-> Tìm thấy {len(tat_ca_van_ban)} văn bản ở Trang 1.")

        if chuyen_trang_2(driver):
            print("-> Đang chuyển sang Trang 2...")
            time.sleep(4)
            trang_2_vb = thu_thập_thong_tin_trang_hien_tai(driver)
            print(f"-> Tìm thấy {len(trang_2_vb)} văn bản ở Trang 2.")
            for item in trang_2_vb:
                if item not in tat_ca_van_ban:
                    tat_ca_van_ban.append(item)

        print(f"==> Tổng số văn bản cần xử lý: {len(tat_ca_van_ban)}")

        danh_sach_ket_qua = []

        for idx, vb in enumerate(tat_ca_van_ban, start=1):
            print(f"\n--- Xử lý văn bản {idx}/{len(tat_ca_van_ban)} ---")
            try:
                driver.get(vb["url"])
                WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                
                files = tim_file_pdf(driver)
                pdf_text = ""
                if files:
                    pdf_url = next(iter(files.keys()))
                    pdf_data = tai_pdf(driver, pdf_url, vb["url"])
                    pdf_text = kiem_tra_va_trich_xuat_chu(pdf_data)

                so_hieu, trich_yeu, han_xu_ly = trich_xuat_thong_tin_chi_tiet(vb["row_text"], pdf_text)
                
                print(f"  -> Số hiệu: {so_hieu} | Hạn: {han_xu_ly}")
                danh_sach_ket_qua.append({
                    "so_hieu": so_hieu,
                    "trich_yeu": trich_yeu,
                    "han_xu_ly": han_xu_ly
                })
            except Exception as e:
                print(f"  [!] Lỗi đọc văn bản này: {e}")

        print("\n5. Đang cập nhật dữ liệu lên Google Sheets...")
        cap_nhat_google_sheets(danh_sach_ket_qua)
        
        print("\n=== HOÀN TẤT ĐỒNG BỘ DANH SÁCH VĂN BẢN ĐẾN! ===")
        return 0

    except Exception as error:
        print(f"\n[!] LỖI HỆ THỐNG: {error}")
        return 1
    finally:
        if driver: 
            driver.quit()

if __name__ == "__main__":
    sys.exit(main())
