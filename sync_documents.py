import builtins
import os
import re
import sys
import warnings
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlparse
from datetime import datetime
import json

import requests
import urllib3
from pypdf import PdfReader
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    builtins.print(*args, **kwargs)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# Cấu hình hệ thống
HOST = "hscvkhcn.dienbien.gov.vn"
URL_LOGIN = f"https://{HOST}/qlvb/index.nsf"
USERNAME = "hoanph.skhcn"
GOOGLE_WEB_APP_URL = os.getenv("GOOGLE_WEB_APP_URL", "")

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
    Quy tắc hạn xử lý:
    - Giao riêng cho Sở KH&CN hoặc giao chung các sở ban ngành có thời hạn -> Có hạn.
    - Chỉ giao riêng cho sở/ngành khác (không dính dáng đến Sở KH&CN) -> Không có hạn.
    """
    so_hieu = "Không xác định"
    trich_yeu = row_text[:120]
    han_xu_ly = "Không có hạn"

    # Bóc tách số hiệu văn bản
    match_so = re.search(r'([A-Za-z0-9/-]+(?:UBND|STC|SKHCN|VPCP)[A-Za-z0-9/-]*)', row_text)
    if match_so:
        so_hieu = match_so.group(1)

    combined_text = row_text + "\n" + pdf_text
    
    lien_quan_skhcn = bool(re.search(r"(?i)(?:sở khoa học và công nghệ|skhcn)", combined_text))
    giao_chung_cac_so = bool(re.search(r"(?i)(?:các sở, ban, ngành|các sở, ban, ngành, đoàn thể|các cơ quan, đơn vị|UBND các huyện, thị xã, thành phố)", combined_text))
    giao_rieng_so_khac = bool(re.search(r"(?i)sở\s+(?:giao thông vận tải|y tế|giáo dục và đào tạo|tài chính|tài nguyên và môi trường|nông nghiệp và phát triển nông thôn|lao động)", combined_text))

    match_han = re.search(r"(?i)(?:trước ngày|chậm nhất ngày|hoàn thành trước ngày|thời hạn báo cáo|thực hiện trước)\s*[:]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", combined_text)
    
    if match_han:
        ngay_tim_thay = match_han.group(1)
        if lien_quan_skhcn or giao_chung_cac_so or not giao_rieng_so_khac:
            han_xu_ly = f"Trước ngày {ngay_tim_thay}"

    return so_hieu, trich_yeu, han_xu_ly

def day_du_lieu_len_google_sheets(danh_sach_ket_qua):
    if not GOOGLE_WEB_APP_URL:
        print("[!] Chưa cấu hình GOOGLE_WEB_APP_URL.")
        return

    try:
        thoi_gian_hien_tai = datetime.now().strftime("%d/11/2026 %H:%M:%S")
        payload = []
        for idx, item in enumerate(danh_sach_ket_qua, start=1):
            payload.append({
                "stt": idx,
                "so_hieu": item["so_hieu"],
                "trich_yeu": item["trich_yeu"],
                "han_xu_ly": item["han_xu_ly"],
                "thoi_gian": thoi_gian_hien_tai
            })
        
        response = requests.post(GOOGLE_WEB_APP_URL, json=payload, timeout=30, verify=False)
        print(f"-> [Google Sheets Web App] Phản hồi: {response.text}")
    except Exception as e:
        print(f"[!] Lỗi đẩy dữ liệu lên Google Sheets: {e}")

def main():
    driver = None
    try:
        print("1. Đang khởi động Chrome...")
        driver = tao_trinh_duyet()

        print("2. Đang đăng nhập hệ thống văn bản...")
        dang_nhap(driver)
        
        print("3. Đang truy cập danh sách văn bản...")
        url_view = f"https://{HOST}/qlvb/vbden.nsf/default?openform&frm=Private_ChoXL?openForm"
        driver.get(url_view)
        
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

        print("\n5. Đang đẩy dữ liệu lên Google Sheets...")
        day_du_lieu_len_google_sheets(danh_sach_ket_qua)
        
        print("\n=== HOÀN TẤT ===")
        return 0

    except Exception as error:
        print(f"\n[!] LỖI: {error}")
        return 1
    finally:
        if driver: 
            driver.quit()

if __name__ == "__main__":
    sys.exit(main())
