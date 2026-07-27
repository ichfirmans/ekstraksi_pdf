from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pdfplumber
import pandas as pd
import re
import os
import io
import posixpath
import dropbox

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- FUNGSI PEMBERSIH ANGKA DARI DESKTOP ---
def to_numeric(value_str):
    try:
        if not value_str or str(value_str).strip() == "0": 
            return 0.0
        clean_val = str(value_str).replace(',', '')
        return float(clean_val)
    except: 
        return 0.0

@app.post("/jalankan-ekstraksi")
def endpoint_ekstraksi(data_folder: dict):
    full_dropbox_path = data_folder.get("path")
    if not full_dropbox_path:
        raise HTTPException(status_code=400, detail="Path Dropbox tidak boleh kosong")

    try:
        dbx = dropbox.Dropbox(
            app_key="eawptwr1w9s6ggl",
            app_secret="dw1syzxk4s25hfz",
            oauth2_refresh_token="gCpLKvPVazwAAAAAAAAAAcC9Epao1MYyHz9FB4uUfLehG9E2z-lfCXGLarnxdBLm"
        )
        
        res = dbx.files_list_folder(full_dropbox_path)
        pdf_files = [entry.path_display for entry in res.entries if entry.name.lower().endswith('.pdf')]
        
        if not pdf_files:
            return {"status": "success", "message": "Tidak ada file PDF di folder ini."}

        rows = []
        for path in pdf_files:
            _, response = dbx.files_download(path)
            file_stream = io.BytesIO(response.content)
            
            with pdfplumber.open(file_stream) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if not text: continue
                    for line in text.split("\n"):
                        line_raw = line.strip()
                        if re.match(r"^\d+\s", line_raw):
                            match_nik = re.search(r"(\d{8})", line_raw)
                            if not match_nik: continue
                            nik = match_nik.group(1)
                            end_nik = match_nik.end()
                            hk = "0"
                            search_area_hk = line_raw[end_nik:]
                            match_hk_pattern = re.search(r"0\D*(\d+)", search_area_hk)
                            if match_hk_pattern:
                                raw_hk = match_hk_pattern.group(1)
                                hk = raw_hk[:2] if len(raw_hk) >= 2 else raw_hk
                                if int(hk) > 30: hk = hk[:1]
                                posisi_hk_di_string = end_nik + match_hk_pattern.start(1)
                                nama_raw = line_raw[end_nik:posisi_hk_di_string]
                                nama = re.sub(r"(L0|LR0|LI0|LA0|K0|D0|K1|HK|0)", "", nama_raw).strip()
                            else: nama = "NAMA TIDAK TERDETEKSI"
                            
                            parts = line_raw.split()
                            jam_biasa, jam_libur = 0.0, 0.0
                            upah_idx = None
                            for i, p in enumerate(parts):
                                if re.search(r"\d{1,3}[,.]\d{3}", p):
                                    upah_idx = i; break
                            
                            if upah_idx is not None:
                                idx_target = upah_idx + 4
                                if idx_target < len(parts):
                                    is_star = "*" in parts[idx_target] or (idx_target > 0 and "*" in parts[idx_target-1])
                                    if is_star:
                                        nilai_target_str = "0"
                                        for s_idx in range(idx_target, len(parts)):
                                            v_check = parts[s_idx].replace(',', '')
                                            if v_check.isdigit() and int(v_check) > 0:
                                                nilai_target_str = parts[s_idx]; break
                                        
                                        # Pakai to_numeric yang sudah distandarkan
                                        n_awal = to_numeric(nilai_target_str)
                                        jam_biasa = n_awal / 31679 if n_awal > 0 else 0.0
                                    else:
                                        # Pakai to_numeric agar aman dari error tipe data
                                        jam_biasa = to_numeric(parts[idx_target])
                                        if idx_target + 2 < len(parts): 
                                            jam_libur = to_numeric(parts[idx_target + 2])
                            
                            rows.append({"NIK": nik, "Nama": nama, "HK": hk, "Jam Lembur Biasa": jam_biasa, "Jam Lembur Libur": jam_libur})

        if rows:
            df = pd.DataFrame(rows)
            df = df.sort_values(by=['NIK', 'HK'], ascending=[True, False]).drop_duplicates(subset=['NIK'], keep='first')
            df.insert(0, "No", range(1, len(df) + 1))
            
            # --- MENCEGAH EXCEL MENJADIKAN ANGKA SEBAGAI WAKTU (26.05.00) ---
            # Kita ubah format titik desimal menjadi koma sebelum disave ke CSV
            df['Jam Lembur Biasa'] = df['Jam Lembur Biasa'].astype(str).str.replace('.', ',')
            df['Jam Lembur Libur'] = df['Jam Lembur Libur'].astype(str).str.replace('.', ',')
            
            csv_bytes = df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8')
            
            folder_name = os.path.basename(full_dropbox_path)
            parent_path = posixpath.dirname(full_dropbox_path)
            parent_folder_name = os.path.basename(parent_path)
            base_filename = f"Ekstraksi_pdf_{folder_name}_{parent_folder_name}"
            
            dbx.files_upload(csv_bytes, f"{parent_path}/{base_filename}.csv", mode=dropbox.files.WriteMode.overwrite)
            
        return {"status": "success", "message": "Ekstraksi dan upload CSV berhasil!"}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
