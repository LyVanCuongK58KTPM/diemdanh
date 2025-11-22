from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
import cv2
import numpy as np
import base64
import os
import io
import json
import pandas as pd 
from datetime import datetime, date
from deepface import DeepFace
from PIL import Image, ImageOps

app = Flask(__name__)
app.secret_key = 'khoa_bi_mat_sieu_cap_vip_pro'

# --- CẤU HÌNH DB ---
db_config = {
    'user': 'avnadmin',
    'password': 'AVNS_uwfAJ91Ub4Jnhc-_pOB', # <--- ĐIỀN MẬT KHẨU DB CỦA BẠN
    'host': 'mysql-2b420606-lyvancuongklbg-6918.e.aivencloud.com',
    'port': 27739,
    'database': 'FaceAttendanceDB',
    'ssl_ca': 'ca.pem'
}

UPLOAD_FOLDER = 'static/faces'
if not os.path.exists(UPLOAD_FOLDER): os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
MODEL_NAME = "VGG-Face"

def get_db_connection():
    return mysql.connector.connect(**db_config)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg'}

# Hàm xử lý ảnh PIL
def process_image_robust(image_source):
    try:
        if hasattr(image_source, 'seek'): image_source.seek(0)
        img_pil = Image.open(image_source)
        img_pil = ImageOps.exif_transpose(img_pil)
        img_pil = img_pil.convert('RGB')
        return np.array(img_pil, dtype=np.uint8)
    except: return None

# ==========================================
# 1. LOGIN / LOGOUT
# ==========================================
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM tai_khoan WHERE username = %s", (username,))
            user = cursor.fetchone()
            conn.close()
            if user and check_password_hash(user['password'], password):
                session['user_id'] = user['user_id']
                session['role'] = user['role']
                session['ho_ten'] = user['ho_ten']
                
                # Chuyển hướng đúng theo vai trò
                if user['role'] == 'admin': return redirect(url_for('dashboard'))
                elif user['role'] == 'giao_vien': return redirect(url_for('teacher_dashboard'))
                else: return redirect(url_for('student_dashboard'))
            else:
                flash('Sai tài khoản hoặc mật khẩu!', 'error')
        except Exception as e:
            flash(f"Lỗi kết nối: {str(e)}", "error")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ==========================================
# 2. ADMIN - QUẢN TRỊ (ĐÃ KHÔI PHỤC LẠI)
# ==========================================
@app.route('/dashboard')
def dashboard():
    if 'role' in session and session['role'] == 'admin':
        return render_template('dashboard.html')
    return redirect(url_for('login'))

@app.route('/create_user', methods=['POST'])
def create_user():
    if 'role' not in session or session['role'] != 'admin': return redirect(url_for('login'))
    
    ho_ten = request.form['ho_ten']
    username = request.form['username']
    password = request.form['password']
    role = request.form['role']
    ma_so = request.form['ma_so']
    hashed_password = generate_password_hash(password)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        conn.start_transaction()
        cursor.execute("INSERT INTO tai_khoan (username, password, ho_ten, email, role) VALUES (%s, %s, %s, %s, %s)", (username, hashed_password, ho_ten, '', role))
        new_id = cursor.lastrowid
        if role == 'sinh_vien':
            cursor.execute("INSERT INTO sinh_vien (ma_sv, ho_ten, user_id) VALUES (%s, %s, %s)", (ma_so, ho_ten, new_id))
        else:
            cursor.execute("INSERT INTO giao_vien (ma_gv, user_id) VALUES (%s, %s)", (ma_so, new_id))
        conn.commit()
        flash('Tạo tài khoản thành công!', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Lỗi: {e}', 'error')
    finally:
        conn.close()
    return redirect(url_for('dashboard'))

@app.route('/upload_sample', methods=['POST'])
def upload_sample():
    if 'role' not in session or session['role'] != 'admin': return redirect(url_for('login'))
    
    ma_so = request.form['ma_so']
    file = request.files['file']
    
    if not file: return redirect(url_for('dashboard'))

    try:
        filename = f"{ma_so}.jpg"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        embedding_objs = DeepFace.represent(img_path=file_path, model_name=MODEL_NAME, enforce_detection=False)
        
        if len(embedding_objs) > 0:
            embedding = embedding_objs[0]["embedding"]
            embedding_json = json.dumps(embedding)

            conn = get_db_connection()
            cursor = conn.cursor()
            
            if "SV" in ma_so.upper():
                sql = "UPDATE sinh_vien SET face_image_path=%s, face_encoding=%s WHERE ma_sv=%s"
            else:
                sql = "UPDATE giao_vien SET face_image_path=%s, face_encoding=%s WHERE ma_gv=%s"
            
            cursor.execute(sql, (file_path, embedding_json, ma_so))
            
            if cursor.rowcount > 0:
                conn.commit()
                flash(f"Train AI thành công cho {ma_so}!", "success")
            else:
                flash(f"Không tìm thấy mã số {ma_so}!", "error")
                if os.path.exists(file_path): os.remove(file_path)
            conn.close()
        else:
            flash("Không tìm thấy mặt trong ảnh!", "error")
            if os.path.exists(file_path): os.remove(file_path)

    except Exception as e:
        flash(f"Lỗi DeepFace: {str(e)}", "error")

    return redirect(url_for('dashboard'))

# ==========================================
# 3. GIÁO VIÊN - FULL TÍNH NĂNG
# ==========================================
@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'role' not in session or session['role'] != 'giao_vien':
        return redirect(url_for('login'))
    
    email = request.form.get('email')
    sdt = request.form.get('sdt')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. Cập nhật Email trong bảng tai_khoan
        cursor.execute("UPDATE tai_khoan SET email = %s WHERE user_id = %s", (email, session['user_id']))
        
        # 2. Cập nhật SĐT trong bảng giao_vien
        cursor.execute("UPDATE giao_vien SET sdt = %s WHERE user_id = %s", (sdt, session['user_id']))
        
        conn.commit()
        flash('Cập nhật thông tin cá nhân thành công!', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Lỗi cập nhật: {str(e)}', 'error')
    finally:
        conn.close()
        
    return redirect(url_for('teacher_dashboard'))

# --- TÍNH NĂNG: CẬP NHẬT LỊCH DẠY (Sửa phòng/giờ) ---
@app.route('/update_schedule', methods=['POST'])
def update_schedule():
    if 'role' not in session or session['role'] != 'giao_vien':
        return redirect(url_for('login'))
    
    lich_id = request.form.get('lich_id')
    phong_hoc = request.form.get('phong_hoc')
    thu = request.form.get('thu')
    gio_bd = request.form.get('gio_bat_dau')
    gio_kt = request.form.get('gio_ket_thuc')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        sql = """
            UPDATE lich_hoc 
            SET phong_hoc = %s, thu_trong_tuan = %s, gio_bat_dau = %s, gio_ket_thuc = %s
            WHERE lich_id = %s
        """
        cursor.execute(sql, (phong_hoc, thu, gio_bd, gio_kt, lich_id))
        conn.commit()
        flash('Cập nhật lịch dạy thành công!', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Lỗi cập nhật lịch: {str(e)}', 'error')
    finally:
        conn.close()
        
    return redirect(url_for('teacher_dashboard'))
@app.route('/teacher_dashboard')
def teacher_dashboard():
    if 'role' not in session or session['role'] != 'giao_vien': return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT gv.*, tk.email, tk.username 
        FROM giao_vien gv 
        JOIN tai_khoan tk ON gv.user_id = tk.user_id 
        WHERE gv.user_id = %s
    """, (session['user_id'],))
    gv_info = cursor.fetchone()

    cursor.execute("""
        SELECT lh.*, mh.ten_mon, l.ten_lop 
        FROM lich_hoc lh
        JOIN mon_hoc mh ON lh.mon_id = mh.mon_id
        JOIN lop_hoc l ON lh.lop_id = l.lop_id
        WHERE lh.gv_id = %s
        ORDER BY lh.thu_trong_tuan, lh.gio_bat_dau
    """, (gv_info['gv_id'],))
    full_schedule = cursor.fetchall()

    cursor.execute("SELECT DISTINCT mon_id, ten_mon FROM mon_hoc")
    ds_mon = cursor.fetchall()
    
    conn.close()
    return render_template('teacher_dashboard.html', gv=gv_info, schedule=full_schedule, ds_mon=ds_mon)

# --- ĐỔI MẬT KHẨU ---
@app.route('/change_password', methods=['POST'])
def change_password():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    old_pass = request.form['old_pass']
    new_pass = request.form['new_pass']
    confirm_pass = request.form['confirm_pass']

    if new_pass != confirm_pass:
        flash('Mật khẩu mới không khớp!', 'error')
        return redirect(url_for('teacher_dashboard'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT password FROM tai_khoan WHERE user_id = %s", (session['user_id'],))
    user = cursor.fetchone()

    if user and check_password_hash(user['password'], old_pass):
        new_hash = generate_password_hash(new_pass)
        cursor.execute("UPDATE tai_khoan SET password = %s WHERE user_id = %s", (new_hash, session['user_id']))
        conn.commit()
        flash('Đổi mật khẩu thành công!', 'success')
    else:
        flash('Mật khẩu cũ không đúng!', 'error')
    
    conn.close()
    return redirect(url_for('teacher_dashboard'))

# --- LỌC LỊCH SỬ ---
@app.route('/filter_attendance', methods=['POST'])
def filter_attendance():
    data = request.json
    mon_id = data.get('mon_id')
    ngay_hoc = data.get('ngay_hoc')
    
    sql = """
        SELECT dd.ngay_diem_danh, sv.ma_sv, sv.ho_ten, l.ten_lop, mh.ten_mon, dd.thoi_gian_vao, dd.trang_thai
        FROM diem_danh dd
        JOIN sinh_vien sv ON dd.sv_id = sv.sv_id
        JOIN lich_hoc lh ON dd.lich_id = lh.lich_id
        JOIN lop_hoc l ON lh.lop_id = l.lop_id
        JOIN mon_hoc mh ON lh.mon_id = mh.mon_id
        WHERE 1=1
    """
    params = []
    if mon_id:
        sql += " AND lh.mon_id = %s"
        params.append(mon_id)
    if ngay_hoc:
        sql += " AND dd.ngay_diem_danh = %s"
        params.append(ngay_hoc)
        
    sql += " ORDER BY dd.ngay_diem_danh DESC, dd.thoi_gian_vao DESC"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(sql, tuple(params))
    results = cursor.fetchall()
    
    for r in results:
        r['ngay_diem_danh'] = str(r['ngay_diem_danh'])
        r['thoi_gian_vao'] = str(r['thoi_gian_vao'])
        
    conn.close()
    return jsonify({'status': 'success', 'data': results})

# --- XUẤT EXCEL ---
@app.route('/export_excel')
def export_excel():
    if 'role' not in session or session['role'] != 'giao_vien': return redirect(url_for('login'))
    
    mon_id = request.args.get('mon_id')
    ngay_hoc = request.args.get('ngay_hoc')

    conn = get_db_connection()
    sql = """
        SELECT dd.ngay_diem_danh AS 'Ngày', sv.ma_sv AS 'Mã SV', sv.ho_ten AS 'Họ Tên', 
               l.ten_lop AS 'Lớp', mh.ten_mon AS 'Môn Học', dd.thoi_gian_vao AS 'Giờ Vào', dd.trang_thai AS 'Trạng Thái'
        FROM diem_danh dd
        JOIN sinh_vien sv ON dd.sv_id = sv.sv_id
        JOIN lich_hoc lh ON dd.lich_id = lh.lich_id
        JOIN lop_hoc l ON lh.lop_id = l.lop_id
        JOIN mon_hoc mh ON lh.mon_id = mh.mon_id
        WHERE 1=1
    """
    params = []
    if mon_id:
        sql += " AND lh.mon_id = %s"
        params.append(mon_id)
    if ngay_hoc:
        sql += " AND dd.ngay_diem_danh = %s"
        params.append(ngay_hoc)

    df = pd.read_sql(sql, conn, params=tuple(params))
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='DiemDanh')
    output.seek(0)
    
    filename = f"DiemDanh_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(output, download_name=filename, as_attachment=True)

# ==========================================
# 4. XỬ LÝ CAMERA & ĐIỂM DANH (GIỮ NGUYÊN)
# ==========================================
@app.route('/get_attendance_list', methods=['POST'])
def get_attendance_list():
    data = request.json
    lich_id = data.get('lich_id')
    today = date.today()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    sql = """SELECT sv.ma_sv, sv.ho_ten, dd.thoi_gian_vao FROM diem_danh dd
             JOIN sinh_vien sv ON dd.sv_id = sv.sv_id
             WHERE dd.lich_id=%s AND dd.ngay_diem_danh=%s ORDER BY dd.thoi_gian_vao DESC"""
    cursor.execute(sql, (lich_id, today))
    rows = cursor.fetchall()
    for r in rows: r['thoi_gian_vao'] = str(r['thoi_gian_vao'])
    conn.close()
    return jsonify({'status': 'success', 'data': rows})

@app.route('/process_attendance', methods=['POST'])
def process_attendance():
    data = request.json
    image_data = data['image']
    lich_id = data['lich_id']
    try:
        header, encoded = image_data.split(",", 1)
        nparr = np.frombuffer(base64.b64decode(encoded), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        embedding_objs = DeepFace.represent(img_path=img, model_name=MODEL_NAME, enforce_detection=False)
        if len(embedding_objs) == 0: return jsonify({'status': 'fail', 'message': 'Không thấy mặt'})
        target_embedding = embedding_objs[0]["embedding"]
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT sv_id, ho_ten, face_encoding FROM sinh_vien JOIN lich_hoc lh ON sinh_vien.lop_id=lh.lop_id WHERE lh.lich_id=%s AND face_encoding IS NOT NULL", (lich_id,))
        students = cursor.fetchall()
        
        detected_name = None
        min_dist = 100
        
        for sv in students:
            db_emb = json.loads(sv['face_encoding'])
            a = np.matmul(np.transpose(target_embedding), db_emb)
            b = np.sum(np.multiply(target_embedding, target_embedding))
            c = np.sum(np.multiply(db_emb, db_emb))
            dist = 1 - (a / (np.sqrt(b) * np.sqrt(c)))
            if dist < 0.4 and dist < min_dist:
                min_dist = dist
                detected_name = sv['ho_ten']
                sv_id_match = sv['sv_id']
        
        if detected_name:
            now = datetime.now().strftime('%H:%M:%S')
            today = date.today()
            cursor.execute("INSERT INTO diem_danh (sv_id, lich_id, ngay_diem_danh, thoi_gian_vao, trang_thai) VALUES (%s, %s, %s, %s, 'co_mat') ON DUPLICATE KEY UPDATE thoi_gian_vao=%s", (sv_id_match, lich_id, today, now, now))
            conn.commit()
            conn.close()
            return jsonify({'status': 'success', 'message': f"Đã điểm danh: {detected_name}"})
        
        conn.close()
        return jsonify({'status': 'unknown', 'message': 'Không nhận diện được'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

# ==========================================
# 5. SINH VIÊN
# ==========================================
@app.route('/student_dashboard')
def student_dashboard():
    if 'role' not in session or session['role'] != 'sinh_vien': return redirect(url_for('login'))
    return render_template('student_dashboard.html', sv={'ho_ten': session['ho_ten']})

if __name__ == '__main__':
    app.run(debug=True)
