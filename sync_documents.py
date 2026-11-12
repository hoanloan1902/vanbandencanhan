import builtins
import os
import re
import sys
import warnings
import unicodedata
from io import BytesIO
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

def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    builtins.print(*args, **kwargs)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

HOST = "hscvkhcn.dienbien.gov.vn"
URL_LOGIN = f"https://{HOST}/qlvb/index.nsf"
USERNAME = "hoanph.skhcn"
GOOGLE_WEB_APP_URL = os.getenv("GOOGLE_WEB_APP_URL", "").strip()

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

def chuan_hoa(text):
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", text).strip()

def bo_dau_nhan(text):
    text = chuan_hoa(text).casefold()
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace("đ", "d")
    text = re.sub(r"\s*/\s*", "/", text)
    return text.rstrip(":").strip()

def lay_gia_tri_o(cell):
    val_text = chuan_hoa(cell.text)
    controls = cell.find_elements(By.CSS_SELECTOR, "input, textarea, select")
    for ctrl in controls:
        if not ctrl.is_displayed():
            continue
        ctype = (ctrl.get_attribute("type") or "").casefold()
        if ctype in {"hidden", "password", "checkbox", "radio"}:
            continue
        v = chuan_hoa(ctrl.get_attribute("value"))
        if v:
            return v
    return val_text

def trich_xuat_tu_trang_chi_tiet(driver):
    """
    Đọc chính xác từng ô trong bảng 'Các thông tin cơ bản' theo đúng giao diện Lotus Notes:
    - Số / ký hiệu: 10152/UBND-KT
    - Ngày đến: 10/11/2026 | Ngày ban hành: 10/11/2026
    - Trích yếu: ...
    """
    so_ky_hieu = ""
    ngay_den = ""
    ngay_ban_hanh = ""
    trich_yeu = ""

    def quet_bang_chi_tiet(drv, depth=0):
        nonlocal so_ky_hieu, ngay_den, ngay_ban_hanh, trich_yeu
        if depth > 4:
            return

        for row in drv.find_elements(By.TAG_NAME, "tr"):
            cells = row.find_elements(By.XPATH, "./th | ./td")
            if len(cells) < 2:
                continue

            for i in range(len(cells) - 1):
                nhan = bo_dau_nhan(cells[i].text)
                if not nhan or len(nhan) > 40:
                    continue

                gia_tri = lay_gia_tri_o(cells[i + 1])
                if not gia_tri:
                    continue

                if nhan in {"so/ky hieu", "so ky hieu", "so hieu"} and not so_ky_hieu:
                    so_ky_hieu = gia_tri
                elif nhan == "ngay den" and not ngay_den:
                    ngay_den = gia_tri
                elif nhan in {"ngay ban hanh", "ngay van ban"} and not ngay_ban_hanh:
                    ngay_ban_hanh = gia_tri
                elif nhan in {"trich yeu", "trich yeu noi dung"} and not trich_yeu:
                    trich_yeu = gia_tri

        for f in drv.find_elements(By.CSS_SELECTOR, "frame, iframe"):
            try:
                drv.switch_to.frame(f)
                quet_bang_chi_tiet(drv, depth + 1)
                drv.switch_to.parent_frame()
            except Exception:
                drv.switch_to.default_content()

    driver.switch_to.default_content()
    quet_bang_chi_tiet(driver)

    ngay_hien_thi = ngay_ban_hanh or ngay_den
    if so_ky_hieu and ngay_hien_thi:
        so_hieu_day_du = f"{so_ky_hieu} ngày {ngay_hien_thi}"
    elif so_ky_hieu:
        so_hieu_day_du = so_ky_hieu
    else:
        so_hieu_day_du = "Không xác định"

    return {
        "so_hieu": so_hieu_day_du,
        "ngay_den": ngay_den,
        "trich_yeu": trich_yeu or "Không có trích yếu"
    }

def lay_moc_thoi_gian_tu_ten_file(filename):
    """
    Bóc tách mốc thời gian từ tên file dongdau để so sánh lấy file mới nhất.
    Ví dụ: dongdau20055_10-11-2026-14-10-03_...pdf -> datetime(2026, 11, 10, 14, 10, 3)
    """
    # Mẫu 1: DD-MM-YYYY-HH-MM-SS (phổ biến nhất trên file dongdau)
    m1 = re.search(r'(\d{2})-(\d{2})-(\d{4})-(\d{2})-(\d{2})-(\d{2})', filename)
    if m1:
        try:
            d, mo, y, h, mi, s = map(int, m1.groups())
            return datetime(y, mo, d, h, mi, s)
        except ValueError:
            pass

    # Mẫu 2: DDMMYYYY_HHMMSS
    m2 = re.search(r'(\d{2})(\d{2})(\d{4})[_-](\d{2})(\d{2})(\d{2})', filename)
    if m2:
        try:
            d, mo, y, h, mi, s = map(int, m2.groups())
            return datetime(y, mo, d, h, mi, s)
        except ValueError:
            pass

    return datetime.min

def tim_file_dongdau_moi_nhat(driver):
    """
    Quét toàn bộ file PDF đính kèm, lọc các file 'dongdau' và chọn duy nhất file có thời gian gần nhất.
    """
    files = []

    def quet_link_pdf(drv, depth=0):
        if depth > 4:
            return
        for link in drv.find_elements(By.CSS_SELECTOR, "a[href]"):
            href = link.get_attribute("href") or ""
            parsed = urlparse(href)
            if parsed.scheme != "https" or parsed.hostname != HOST:
                continue
            path = unquote(parsed.path)
            if "/$file/" not in path.casefold():
                continue
            filename = path.rsplit("/", 1)[-1]
            if filename.casefold().endswith(".pdf"):
                if not any(u == href for u, _ in files):
                    files.append((href, filename))

        for f in drv.find_elements(By.CSS_SELECTOR, "frame, iframe"):
            try:
                drv.switch_to.frame(f)
                quet_link_pdf(drv, depth + 1)
                drv.switch_to.parent_frame()
            except Exception:
                drv.switch_to.default_content()

    driver.switch_to.default_content()
    quet_link_pdf(driver)

    if not files:
        return None, None

    # Lọc các file có chữ 'dongdau'
    dongdau_files = [(u, f) for u, f in files if "dongdau" in f.casefold()]
    if dongdau_files:
        # Sắp xếp theo thời gian giảm dần (mới nhất lên đầu)
        dongdau_files.sort(key=lambda item: lay_moc_thoi_gian_tu_ten_file(item[1]), reverse=True)
        return dongdau_files[0]

    # Dự phòng nếu văn bản chưa đóng dấu thì lấy file PDF đầu tiên
    return files[0]

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

def xac_dinh_han_xu_ly(trich_yeu, pdf_text):
    han_xu_ly = "Không có hạn"
    combined_text = trich_yeu + "\n" + pdf_text
    
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
        print("[!] Chưa cấu hình GOOGLE_WEB_APP_URL trong GitHub Secrets.")
        return

    try:
        thoi_gian_hien_tai = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
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
        print(f"==> Tiến hành quét chi tiết {len(tat_ca_van_ban)} văn bản đầu tiên.")
        
        danh_sach_ket_qua = []

        for idx, vb in enumerate(tat_ca_van_ban, start=1):
            print(f"\n--- Xử lý văn bản {idx}/{len(tat_ca_van_ban)} ---")
            try:
                driver.get(vb["url"])
                WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                time.sleep(1)

                # Bóc tách chuẩn xác các trường từ trang chi tiết
                thong_tin = trich_xuat_tu_trang_chi_tiet(driver)
                
                # Chọn duy nhất file dongdau có thời gian mới nhất để quét hạn
                pdf_url, fname = tim_file_dongdau_moi_nhat(driver)
                pdf_text = ""
                if pdf_url:
                    print(f"  -> File đóng dấu mới nhất: {fname}")
                    try:
                        pdf_data = tai_pdf(driver, pdf_url, vb["url"])
                        pdf_text = kiem_tra_va_trich_xuat_chu(pdf_data)
                    except Exception:
                        pass

                han_xu_ly = xac_dinh_han_xu_ly(thong_tin["trich_yeu"], pdf_text)
                
                print(f"  -> Số hiệu: {thong_tin['so_hieu']} | Ngày đến: {thong_tin['ngay_den']}")
                print(f"  -> Trích yếu: {thong_tin['trich_yeu'][:70]}...")
                print(f"  -> Hạn xử lý: {han_xu_ly}")

                danh_sach_ket_qua.append({
                    "so_hieu": thong_tin["so_hieu"],
                    "trich_yeu": thong_tin["trich_yeu"],
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
