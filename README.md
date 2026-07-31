# PC Monitor & Remote Management (Python + Flask)

Sistem sederhana untuk memantau dan mengelola PC client dari satu dashboard web.
Terdiri dari 2 bagian:

- **`server/`** — Flask + SocketIO, jadi pusat kendali & dashboard admin
- **`client/`** — agent Python yang dipasang di tiap PC yang ingin dipantau

⚠️ **Catatan penting:** Pasang agent hanya di perangkat yang memang Anda kelola
(kantor/sekolah/organisasi Anda sendiri), dan beri tahu pengguna perangkat
bahwa agent monitoring terpasang. Jangan gunakan untuk memantau perangkat
orang lain tanpa izin — itu melanggar hukum di hampir semua negara.

---

## 1. Persiapan Server

### a. Install dependency
```bash
cd server
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### b. Atur konfigurasi
```bash
cp .env.example .env
```
Edit `.env` dan ganti:
- `SECRET_KEY` — string acak panjang
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — login ke dashboard
- `CLIENT_API_KEY` — kunci rahasia yang harus sama persis di client agent

Lalu load env sebelum run (atau pakai `python-dotenv`, sudah termasuk di requirements — cukup tambahkan baris `from dotenv import load_dotenv; load_dotenv()` di awal `app.py` jika ingin otomatis baca `.env`. Atau paling mudah, set manual:

```bash
export SECRET_KEY="..." ADMIN_USERNAME="..." ADMIN_PASSWORD="..." CLIENT_API_KEY="..."
# Windows PowerShell: $env:SECRET_KEY="..."
```

### c. Jalankan server
```bash
python app.py
```
Server jalan di `http://0.0.0.0:5000`. Buka `http://localhost:5000` dari browser,
login pakai username/password yang sudah diatur.

> **Untuk produksi:** jangan pakai `debug=True`, jalankan di belakang reverse proxy
> (nginx) dengan HTTPS/WSS, dan gunakan database yang lebih kuat (PostgreSQL) jika
> jumlah client banyak.

---

## 2. Persiapan Client Agent (di tiap PC yang dipantau)

### a. Install dependency
```bash
cd client
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### b. Atur target server
```bash
export MONITOR_SERVER_URL="http://IP-SERVER-ANDA:5000"
export MONITOR_API_KEY="key-rahasia-yang-sama-dengan-server"
# Windows PowerShell:
# $env:MONITOR_SERVER_URL="http://IP-SERVER-ANDA:5000"
# $env:MONITOR_API_KEY="key-rahasia-yang-sama-dengan-server"
```
Atau salin `config.json.example` menjadi `config.json` dan isi di sana
(cara ini yang dipakai versi .exe — lihat bagian bawah).

### c. Jalankan agent
```bash
python agent.py
```
Agent akan otomatis terdaftar ke server dan mulai mengirim status setiap 5 detik.

### d. Ingin PC client tinggal "install" tanpa Python? 
Lihat **`client/BUILD_EXE.md`** — panduan build `agent.py` menjadi satu file
installer `.exe` (pakai PyInstaller + Inno Setup) yang tinggal dijalankan di
tiap PC Windows, lengkap dengan wizard konfigurasi & auto-start saat login.

### d. (Opsional) Jalankan otomatis saat PC menyala
- **Windows:** buat shortcut `agent.py` di folder Startup
  (`shell:startup`), atau daftarkan sebagai Windows Service pakai `nssm`.
- **Linux:** buat systemd service:
  ```ini
  # /etc/systemd/system/pc-monitor-agent.service
  [Unit]
  Description=PC Monitor Agent
  After=network.target

  [Service]
  ExecStart=/path/ke/venv/bin/python /path/ke/client/agent.py
  Environment=MONITOR_SERVER_URL=http://IP-SERVER-ANDA:5000
  Environment=MONITOR_API_KEY=key-rahasia-anda
  Restart=always
  User=youruser

  [Install]
  WantedBy=multi-user.target
  ```
  Lalu: `sudo systemctl enable --now pc-monitor-agent`
- **macOS:** gunakan `launchd` (buat file plist di `~/Library/LaunchAgents/`).

---

## 3. Menggunakan Dashboard

1. Buka `http://IP-SERVER-ANDA:5000` → login.
2. Setiap PC yang menjalankan agent akan otomatis muncul sebagai kartu,
   lengkap dengan status online/offline dan grafik CPU/RAM/Disk realtime.
3. Aksi yang tersedia per client:
   - 🖥️ **Kendali Jarak Jauh** — lihat layar client secara live dan kendalikan
     mouse/keyboard langsung dari dashboard (klik, ketik teks, scroll, tombol
     navigasi dasar). Kombinasi tombol seperti Ctrl+C belum didukung.
   - 🔒 **Lock** — kunci layar client
   - 📸 **Screenshot** — ambil screenshot (tersimpan di folder client saat ini)
   - 💬 **Pesan** — kirim notifikasi/pesan pop-up ke layar client
   - 🔄 **Restart** — restart PC (ada konfirmasi)
   - ⏻ **Shutdown** — matikan PC (ada konfirmasi)

Saat sesi Kendali Jarak Jauh dimulai, client otomatis menampilkan notifikasi
pop-up "PC ini sedang dikendalikan jarak jauh oleh admin" ke pengguna PC —
ini penting untuk transparansi, jangan dihapus/dimatikan kecuali kebijakan
organisasi Anda memang mengizinkan monitoring diam-diam dengan payung hukum
yang jelas.

Semua perintah dicatat di tabel `CommandLog` (bisa dilihat lewat endpoint `/api/logs`).

---

## 4. Struktur Proyek
```
pc-monitor/
├── server/
│   ├── app.py              # Server Flask + SocketIO + REST API
│   ├── requirements.txt
│   ├── .env.example
│   └── templates/
│       ├── login.html
│       └── dashboard.html
└── client/
    ├── agent.py             # Agent yang jalan di PC target
    └── requirements.txt
```

## 5. Menambah Fitur Lanjutan (ide pengembangan)
- Autentikasi client per-device (bukan satu API key untuk semua) agar bisa
  mencabut akses satu PC tanpa mengganti key semua PC.
- Enkripsi HTTPS/WSS (pakai nginx + certbot atau `flask-talisman`).
- Upload screenshot ke server (saat ini disimpan lokal di client saja) via
  endpoint upload terpisah dengan validasi ukuran/format file.
- Riwayat grafik historis (simpan tiap heartbeat ke tabel time-series, atau
  pakai InfluxDB/Prometheus untuk skala besar).
- Role-based access (admin biasa vs super admin) jika banyak operator.
- Grouping client berdasarkan departemen/lokasi.

## 6. Keamanan — Wajib Dibaca
- Jangan pernah commit `.env` atau API key ke git publik.
- Ganti `ADMIN_PASSWORD` dan `CLIENT_API_KEY` bawaan sebelum dipakai.
- Command yang bisa dieksekusi client **dibatasi whitelist** (`ALLOWED_COMMANDS`
  di `app.py` dan `COMMAND_HANDLERS` di `agent.py`) — jangan tambahkan command
  bebas/`eval`/`exec` tanpa validasi ketat, karena itu bisa disalahgunakan
  sebagai backdoor jika server disusupi.
- Pertimbangkan menambahkan rate limiting di endpoint `/api/command` agar
  tidak disalahgunakan (misal pakai `Flask-Limiter`).
