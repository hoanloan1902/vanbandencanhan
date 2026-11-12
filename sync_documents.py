import builtins
import os
import re
import sys
import warnings
import unicodedata
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

def chuan_hoa(text):
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", text).strip()

def bo_dau(text):
    text = chuan_hoa(text).casefold()
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return text.replace("đ", "d")

def trich_xuat_tu_trang_chi_tiet(driver, fallback_row_text):
    """
    Sử dụng logic chuẩn hóa nhãn để quét trực tiếp các trường Số hiệu và Trích yếu từ trang chi tiết văn bản.
    """
    so_hieu = "Không xác định"
    trich_yeu = ""

    try:
        rows = driver.find_elements(By.TAG_NAME, "tr")
        for row in rows:
            cells = row.find_elements(By.XPATH, "./th | ./td")
            if not cells:
                continue
            
            # Kiểm tra xem hàng này có chứa nhãn cần tìm không
            for i, cell in enumerate(cells):
                label_text = bo_dau(cell.text)
                
                # Nếu tìm thấy nhãn Số ký hiệu
                if any(k in label_text for k in ["so/ky hieu", "so ky hieu", "so hieu", "so van ban"]):
                    # Giá trị thường nằm ở ô kế tiếp (cell tiếp theo) hoặc lấy giá trị input trong ô đó
                    if i + 1 < len(cells):
                        val_cell = cells[i + 1]
                        val_text = chuan_hoa(val_cell.text)
                        # Kiểm tra xem có input/textarea không
                        controls = val_cell.find_elements(By.CSS_SELECTOR, "input, textarea")
                        for ctrl in controls:
                            if ctrl.is_displayed():
                                v = chuan_hoa(ctrl.get_attribute("value"))
                                if v:
                                    val_text = v
                                    break
                        if val_text and len(val_text) < 50:
                            so_hieu = val_text
                            
                # Nếu tìm thấy nhãn Trích yếu
                elif any(k in label_text for k in ["trich yeu", "noi dung", "trich yeu noi dung"]):
                    if i + 1 < len(cells):
                        val_cell = cells[i + 1]
                        val_text = chuan_hoa(val_cell.text)
                        controls = val_cell.find_elements(By.CSS_SELECTOR, "input, textarea")
                        for ctrl in controls:
                            if ctrl.is_displayed():
                                v = chuan_hoa(ctrl.get_attribute("value"))
                                if v:
                                    val_text = v
                                    break
                        if val_text:
                            trich_yeu = val_text
    except Exception:
        pass

    # Fallback dự phòng nếu trang chi tiết cấu trúc khác biệt
    if so_hieu == "Không xác định":
        match_so = re.search(r'\b([0-9]+[/-][A-Za-z0-9\-./]+(?:UBND|STC|SKHCN|VPCP|TƯ|BXD|BKHCN|BNN|BTC|BYT|SXD|TTTH)[A-Za-z0-9\-./]*)\b', fallback_row_text)
        if match_so:
            so_hieu = match_so.group(1).strip()

    if not trich_yeu:
        clean_row = re.sub(r'^\d+[\s\.]+', '', fallback_row_text).strip()
        trich_yeu = clean_row if len(clean_row) > 10 else "Không có trích yếu"

    return so_hieu, trich_yeu

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

def xac_dinh_han_xu_ly(row_text, pdf_text):
    han_xu_ly = "Không có hạn"
    combined_text = row_text + "\n" + pdf_text
    
    lien_quan_skhcn = bool(re.search(r"(?i)(?:sở khoa học và công nghệ|skhcn)", combined_text))
    giao_chung_cac_so = bool(re.search(r"(?i)(?:các sở, ban, ngành|các sở, ban, ngành, đoàn thể|các cơ quan, đơn vị|UBND các huyện, thị xã, thành phố)", combined_text))
    giao_rieng_so_khac = bool(re.search(r"(?i)sở\s+(?:giao thông vận tải|y tế|giáo dục và đào tạo|tài chính|tài nguyên và môi trường|nông nghiệp và phát triển nông thôn|lao động)", combined_text))

    match_han = re.search(r"(?i)(?:trước ngày|chậm nhất ngày|hoàn thành trước ngày|thời hạn báo cáo|thực hiện trước)\s*[:]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", combined_text)
    
    if match_han:
        ngay_tim_thay = match_han.group(1)
        if lien_quan_skhcn or giao_chung_cac_so or not giao_rieng_so_khac:
            han_xu_ly = f"Trước ngày {ngay_tim_thay}"

    return han_xu_ly

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
        
        # Test nhanh 10 văn bản đầu tiên
        tat_ca_van_ban = tat_ca_van_ban[:10]
        print(f"==> Tiến hành quét chi tiết {len(tat_ca_van_ban)} văn bản.")
        
        danh_sach_ket_qua = []

        for idx, vb in enumerate(tat_ca_van_ban, start=1):
            print(f"\n--- Xử lý văn bản {idx}/10 ---")
            try:
                driver.get(vb["url"])
                WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                
                # Lấy số hiệu và trích yếu chuẩn xác trực tiếp từ trang chi tiết
                so_hieu, trich_yeu = trich_xuat_tu_trang_chi_tiet(driver, vb["row_text"])
                
                # Lấy thêm nội dung PDF để dò hạn xử lý
                files = tim_file_pdf(driver)
                pdf_text = ""
                if files:
                    pdf_url = next(iter(files.keys()))
                    try:
                        pdf_data = tai_pdf(driver, pdf_url, vb["url"])
                        pdf_text = kiem_tra_va_trich_xuat_chu(pdf_data)
                    except Exception:
                        pass

                han_xu_ly = xac_dinh_han_xu_ly(vb["row_text"], pdf_text)
                
                print(f"  -> Số hiệu: {so_hieu} | Trích yếu: {trich_yeu[:40]}... | Hạn: {han_xu_ly}")
                danh_sach_ket_qua.append({
                    "so_hieu": so_hieu,
                    "trich_yeu": trich_yeu,
                    "han_xu_ly": han_xu_ly
                })
            except Exception as e:
                print(f"  [!] Lỗi xử lý văn bản này: {e}")

        print("\n5. Đang đẩy dữ liệu lên Google Sheets...")
        day_du_lieu_len_google_sheets(danh_sach_ket_qua)
        
        print("\n=== HOÀN TẤT TEST! ===")
        return 0

    except Exception as error:
        print(f"\n[!] LỖI: {error}")
        return 1
    finally:
        if driver: 
            driver.quit()

if __name__ == "__main__":
    sys.exit(main())
