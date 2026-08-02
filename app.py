"""
PC Monitor & Remote Management - Server
========================================
Flask + Flask-SocketIO server yang menerima laporan status dari client agent
dan mengirim perintah remote ke client tertentu.

Menjalankan:
    pip install -r requirements.txt
    python app.py


"""

import os
import secrets
import datetime
import hashlib
import hmac
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, disconnect, join_room, leave_room

# ---------------------------------------------------------------------------
# Konfigurasi dasar
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Baca file .env di folder yang sama (jika ada) supaya SECRET_KEY,
# ADMIN_USERNAME, ADMIN_PASSWORD, CLIENT_API_KEY otomatis terisi tanpa
# perlu export manual tiap kali membuka terminal baru.
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'monitor.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Ganti ini di file .env atau environment variable untuk produksi!
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
# API key yang harus dimiliki setiap client agent untuk bisa connect
CLIENT_API_KEY = os.environ.get("CLIENT_API_KEY", "ganti-dengan-key-rahasia-anda")

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# Whitelist perintah yang boleh dieksekusi di client (keamanan!)
ALLOWED_COMMANDS = {"lock", "shutdown", "restart", "message", "screenshot", "kill_process"}

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(64), unique=True, nullable=False)  # UUID dari agent
    hostname = db.Column(db.String(128))
    ip_address = db.Column(db.String(64))
    os_info = db.Column(db.String(128))
    cpu_percent = db.Column(db.Float, default=0)
    ram_percent = db.Column(db.Float, default=0)
    disk_percent = db.Column(db.Float, default=0)
    uptime_seconds = db.Column(db.Integer, default=0)
    is_online = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    sid = db.Column(db.String(64))  # socket session id saat ini


class CommandLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(64))
    command = db.Column(db.String(64))
    payload = db.Column(db.String(256))
    sent_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    status = db.Column(db.String(32), default="sent")  # sent / delivered / failed


class AdminUser(db.Model):
    """Akun admin yang bisa login ke dashboard."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    is_superadmin = db.Column(db.Boolean, default=False)  # superadmin tidak bisa dihapus
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def set_password(self, password):
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
        self.password_hash = f"{salt}${h.hex()}"

    def check_password(self, password):
        try:
            salt, stored_hash = self.password_hash.split("$", 1)
            h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
            return hmac.compare_digest(h.hex(), stored_hash)
        except Exception:
            return False


def _ensure_default_admin():
    """Buat akun admin default dari env var jika tabel AdminUser masih kosong."""
    if AdminUser.query.count() == 0:
        admin = AdminUser(
            username=ADMIN_USERNAME,
            is_superadmin=True,
        )
        admin.set_password(ADMIN_PASSWORD)
        db.session.add(admin)
        db.session.commit()
        print(f"[+] Akun admin default dibuat: {ADMIN_USERNAME}")


with app.app_context():
    db.create_all()
    _ensure_default_admin()


# ---------------------------------------------------------------------------
# Auth helper (untuk dashboard admin)
# ---------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        user = AdminUser.query.filter_by(username=u).first()
        if user and user.check_password(p):
            session["logged_in"] = True
            session["admin_username"] = user.username
            session["is_superadmin"] = user.is_superadmin
            return redirect(url_for("dashboard"))
        error = "Username atau password salah"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard routes
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/clients")
@login_required
def api_clients():
    clients = Client.query.all()
    return jsonify([{
        "client_id": c.client_id,
        "hostname": c.hostname,
        "ip_address": c.ip_address,
        "os_info": c.os_info,
        "cpu_percent": c.cpu_percent,
        "ram_percent": c.ram_percent,
        "disk_percent": c.disk_percent,
        "uptime_seconds": c.uptime_seconds,
        "is_online": c.is_online,
        "last_seen": c.last_seen.isoformat() if c.last_seen else None,
    } for c in clients])


@app.route("/api/command", methods=["POST"])
@login_required
def api_send_command():
    data = request.get_json(force=True)
    client_id = data.get("client_id")
    command = data.get("command")
    payload = data.get("payload", "")

    if command not in ALLOWED_COMMANDS:
        return jsonify({"error": "Command tidak diizinkan"}), 400

    client = Client.query.filter_by(client_id=client_id).first()
    if not client or not client.is_online or not client.sid:
        return jsonify({"error": "Client tidak online"}), 404

    log = CommandLog(client_id=client_id, command=command, payload=str(payload))
    db.session.add(log)
    db.session.commit()

    socketio.emit("remote_command", {"command": command, "payload": payload}, to=client.sid)
    return jsonify({"status": "sent"})


@app.route("/api/logs")
@login_required
def api_logs():
    logs = CommandLog.query.order_by(CommandLog.sent_at.desc()).limit(50).all()
    return jsonify([{
        "client_id": l.client_id,
        "command": l.command,
        "payload": l.payload,
        "sent_at": l.sent_at.isoformat(),
        "status": l.status,
    } for l in logs])


@app.route("/api/clients/<client_id>/rename", methods=["PATCH"])
@login_required
def api_rename_client(client_id):
    """Ganti nama tampilan (hostname) client."""
    data = request.get_json(force=True)
    new_name = (data.get("hostname") or "").strip()
    if not new_name:
        return jsonify({"error": "Nama tidak boleh kosong"}), 400
    client = Client.query.filter_by(client_id=client_id).first()
    if not client:
        return jsonify({"error": "Client tidak ditemukan"}), 404
    client.hostname = new_name
    db.session.commit()
    return jsonify({"status": "ok", "hostname": client.hostname})


@app.route("/api/clients/<client_id>/edit", methods=["PATCH"])
@login_required
def api_edit_client(client_id):
    """Edit informasi client (hostname, ip_address, os_info)."""
    data = request.get_json(force=True)
    client = Client.query.filter_by(client_id=client_id).first()
    if not client:
        return jsonify({"error": "Client tidak ditemukan"}), 404
    if "hostname" in data and data["hostname"].strip():
        client.hostname = data["hostname"].strip()
    if "ip_address" in data:
        client.ip_address = data["ip_address"].strip()
    if "os_info" in data:
        client.os_info = data["os_info"].strip()
    db.session.commit()
    return jsonify({"status": "ok"})


@app.route("/api/clients/<client_id>", methods=["DELETE"])
@login_required
def api_delete_client(client_id):
    """Hapus client dari database."""
    client = Client.query.filter_by(client_id=client_id).first()
    if not client:
        return jsonify({"error": "Client tidak ditemukan"}), 404
    db.session.delete(client)
    db.session.commit()
    return jsonify({"status": "deleted"})


# ---------------------------------------------------------------------------
# User Management API
# ---------------------------------------------------------------------------
@app.route("/api/users")
@login_required
def api_list_users():
    """Daftar semua akun admin."""
    users = AdminUser.query.order_by(AdminUser.created_at).all()
    return jsonify([{
        "id": u.id,
        "username": u.username,
        "is_superadmin": u.is_superadmin,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    } for u in users])


@app.route("/api/users", methods=["POST"])
@login_required
def api_create_user():
    """Tambah akun admin baru."""
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "Username dan password wajib diisi"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password minimal 6 karakter"}), 400
    if AdminUser.query.filter_by(username=username).first():
        return jsonify({"error": "Username sudah digunakan"}), 409
    user = AdminUser(username=username, is_superadmin=False)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify({"status": "created", "id": user.id, "username": user.username}), 201


@app.route("/api/users/<int:user_id>", methods=["PATCH"])
@login_required
def api_edit_user(user_id):
    """Ubah username atau password akun admin."""
    data = request.get_json(force=True)
    user = AdminUser.query.get(user_id)
    if not user:
        return jsonify({"error": "User tidak ditemukan"}), 404
    new_username = (data.get("username") or "").strip()
    new_password = data.get("password", "")
    if new_username and new_username != user.username:
        if AdminUser.query.filter_by(username=new_username).first():
            return jsonify({"error": "Username sudah digunakan"}), 409
        user.username = new_username
    if new_password:
        if len(new_password) < 6:
            return jsonify({"error": "Password minimal 6 karakter"}), 400
        user.set_password(new_password)
    db.session.commit()
    return jsonify({"status": "ok", "username": user.username})


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@login_required
def api_delete_user(user_id):
    """Hapus akun admin (superadmin tidak bisa dihapus)."""
    user = AdminUser.query.get(user_id)
    if not user:
        return jsonify({"error": "User tidak ditemukan"}), 404
    if user.is_superadmin:
        return jsonify({"error": "Akun superadmin tidak bisa dihapus"}), 403
    # Jangan hapus diri sendiri
    if user.username == session.get("admin_username"):
        return jsonify({"error": "Tidak bisa menghapus akun sendiri"}), 403
    db.session.delete(user)
    db.session.commit()
    return jsonify({"status": "deleted"})


# ---------------------------------------------------------------------------
# SocketIO events - komunikasi dengan client agent
# ---------------------------------------------------------------------------
@socketio.on("connect")
def handle_connect(auth):
    """Ada 2 jenis koneksi yang diizinkan:
    1. Browser dashboard admin -> sudah login (Flask session cookie)
    2. Client agent -> wajib kirim API key yang valid
    """
    if session.get("logged_in"):
        print(f"[+] Dashboard admin connected, sid={request.sid}")
        return

    api_key = None
    if auth and isinstance(auth, dict):
        api_key = auth.get("api_key")
    if api_key == CLIENT_API_KEY:
        print(f"[+] Client agent connected, sid={request.sid}")
        return

    disconnect()
    return False


@socketio.on("register")
def handle_register(data):
    """Agent mengirim identitas awal dirinya."""
    client_id = data.get("client_id")
    if not client_id:
        return

    client = Client.query.filter_by(client_id=client_id).first()
    if not client:
        client = Client(client_id=client_id)
        db.session.add(client)

    client.hostname = data.get("hostname", "unknown")
    client.ip_address = request.remote_addr
    client.os_info = data.get("os_info", "")
    client.is_online = True
    client.sid = request.sid
    client.last_seen = datetime.datetime.utcnow()
    db.session.commit()

    emit("registered", {"status": "ok"})
    print(f"[+] Client registered: {client.hostname} ({client_id})")


@socketio.on("heartbeat")
def handle_heartbeat(data):
    """Agent mengirim status sistem berkala (CPU, RAM, dll)."""
    client_id = data.get("client_id")
    client = Client.query.filter_by(client_id=client_id).first()
    if not client:
        return

    client.cpu_percent = data.get("cpu_percent", 0)
    client.ram_percent = data.get("ram_percent", 0)
    client.disk_percent = data.get("disk_percent", 0)
    client.uptime_seconds = data.get("uptime_seconds", 0)
    client.is_online = True
    client.sid = request.sid
    client.last_seen = datetime.datetime.utcnow()
    db.session.commit()

    socketio.emit("client_update", {
        "client_id": client.client_id,
        "hostname": client.hostname,
        "cpu_percent": client.cpu_percent,
        "ram_percent": client.ram_percent,
        "disk_percent": client.disk_percent,
        "uptime_seconds": client.uptime_seconds,
        "is_online": True,
    }, to="dashboard_room")


@socketio.on("command_result")
def handle_command_result(data):
    """Agent melaporkan hasil eksekusi command (opsional, untuk logging)."""
    client_id = data.get("client_id")
    command = data.get("command")
    print(f"[i] Result from {client_id}: {command} -> {data.get('result')}")


@socketio.on("disconnect")
def handle_disconnect():
    client = Client.query.filter_by(sid=request.sid).first()
    if client:
        client.is_online = False
        db.session.commit()
        socketio.emit("client_update", {
            "client_id": client.client_id,
            "is_online": False,
        }, to="dashboard_room")
        print(f"[-] Client disconnected: {client.hostname}")


@socketio.on("join_dashboard")
def handle_join_dashboard():
    """Browser dashboard join room untuk terima update realtime."""
    if not session.get("logged_in"):
        disconnect()
        return
    join_room("dashboard_room")


# --- Remote Control (lihat layar + kendali mouse/keyboard) ----------------
@socketio.on("start_remote_session")
def handle_start_remote_session(data):
    """Admin membuka sesi kendali jarak jauh ke satu client."""
    if not session.get("logged_in"):
        return
    client_id = data.get("client_id")
    client = Client.query.filter_by(client_id=client_id).first()
    if not client or not client.is_online or not client.sid:
        emit("remote_session_error", {"client_id": client_id, "error": "Client tidak online"})
        return

    join_room(f"control_{client_id}")
    socketio.emit("start_remote_session", {}, to=client.sid)
    print(f"[i] Admin mulai kendali jarak jauh: {client.hostname}")


@socketio.on("stop_remote_session")
def handle_stop_remote_session(data):
    if not session.get("logged_in"):
        return
    client_id = data.get("client_id")
    leave_room(f"control_{client_id}")

    client = Client.query.filter_by(client_id=client_id).first()
    if client and client.sid:
        socketio.emit("stop_remote_session", {}, to=client.sid)
    print(f"[i] Sesi kendali jarak jauh dihentikan: {client_id}")


@socketio.on("screen_frame")
def handle_screen_frame(data):
    """Client agent mengirim frame layar -> teruskan ke admin yang sedang mengontrolnya."""
    client_id = data.get("client_id")
    if not client_id:
        return
    socketio.emit("screen_frame", data, to=f"control_{client_id}")


@socketio.on("remote_input")
def handle_remote_input(data):
    """Admin mengirim aksi mouse/keyboard -> teruskan ke client yang bersangkutan."""
    if not session.get("logged_in"):
        return
    client_id = data.get("client_id")
    client = Client.query.filter_by(client_id=client_id).first()
    if client and client.sid:
        socketio.emit("remote_input", data, to=client.sid)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print(" PC Monitor Server berjalan di http://0.0.0.0:5110")
    print(f" Admin login  : {ADMIN_USERNAME} / {ADMIN_PASSWORD}")
    print(f" Client API key: {CLIENT_API_KEY}")
    print(" -> Ganti kredensial di atas sebelum dipakai produksi!")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=5110, debug=True)
