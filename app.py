
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, csv, io
from functools import wraps
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "inventory.db")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      name TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'requester'
    );
    CREATE TABLE IF NOT EXISTS materials(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      unit TEXT NOT NULL DEFAULT 'ชิ้น',
      stock INTEGER NOT NULL DEFAULT 0,
      photo TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS requests(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      material_id INTEGER NOT NULL,
      qty INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'รอตรวจสอบ',
      created_at TEXT NOT NULL,
      approved_at TEXT,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(material_id) REFERENCES materials(id)
    );
    """)
    if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        con.execute("INSERT INTO users(username,password_hash,name,role) VALUES(?,?,?,?)",
                    ("admin", generate_password_hash("admin123"), "ผู้ดูแลระบบ", "admin"))
        con.execute("INSERT INTO users(username,password_hash,name,role) VALUES(?,?,?,?)",
                    ("requester", generate_password_hash("1234"), "ผู้เบิกทดลอง", "requester"))
    if con.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0:
        sample = [
            ("MAC001","ปากกาลูกลื่น","ด้าม",50,""),
            ("MAC002","กระดาษ A4","รีม",20,""),
            ("MAC003","ดินสอ","แท่ง",30,""),
            ("MAC004","แฟ้มเอกสาร","แฟ้ม",15,""),
            ("MAC005","กระดาษโน้ต","แพ็ก",25,""),
        ]
        con.executemany("INSERT INTO materials(code,name,unit,stock,photo) VALUES(?,?,?,?,?)", sample)
    con.commit(); con.close()

def login_required(f):
    @wraps(f)
    def w(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return w

def admin_required(f):
    @wraps(f)
    def w(*args, **kwargs):
        if session.get("role") != "admin":
            flash("เฉพาะผู้ดูแลระบบเท่านั้น", "error")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return w

@app.route("/")
def index():
    return redirect(url_for("home"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username=request.form["username"].strip()
        password=request.form["password"]
        con=db(); u=con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone(); con.close()
        if u and check_password_hash(u["password_hash"], password):
            session.update(user_id=u["id"], name=u["name"], role=u["role"])
            return redirect(url_for("home"))
        flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง","error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/home")
@login_required
def home():
    con=db()
    materials=con.execute("SELECT * FROM materials ORDER BY name").fetchall()
    if session["role"]=="admin":
        reqs=con.execute("""SELECT r.*,u.name requester,m.name material,m.code
                            FROM requests r JOIN users u ON u.id=r.user_id
                            JOIN materials m ON m.id=r.material_id
                            ORDER BY r.id DESC""").fetchall()
    else:
        reqs=con.execute("""SELECT r.*,m.name material,m.code FROM requests r
                            JOIN materials m ON m.id=r.material_id
                            WHERE r.user_id=? ORDER BY r.id DESC""",(session["user_id"],)).fetchall()
    con.close()
    return render_template("home.html", materials=materials, reqs=reqs)

@app.post("/request")
@login_required
def create_request():
    try:
        mid=int(request.form["material_id"]); qty=int(request.form["qty"])
        if qty <= 0: raise ValueError()
    except:
        flash("กรุณาระบุจำนวนที่ถูกต้อง","error"); return redirect(url_for("home"))
    con=db(); m=con.execute("SELECT * FROM materials WHERE id=?",(mid,)).fetchone()
    if not m:
        con.close(); flash("ไม่พบวัสดุ","error"); return redirect(url_for("home"))
    if qty > m["stock"]:
        con.close(); flash(f"จำนวนที่ขอเบิกเกินคงเหลือ ({m['stock']} {m['unit']})","error"); return redirect(url_for("home"))
    con.execute("""INSERT INTO requests(user_id,material_id,qty,status,created_at)
                   VALUES(?,?,?,?,?)""",(session["user_id"],mid,qty,"รอตรวจสอบ",datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    con.commit(); con.close()
    flash("ส่งรายการเบิกแล้ว","success"); return redirect(url_for("home"))

@app.post("/approve/<int:rid>")
@admin_required
def approve(rid):
    con=db()
    r=con.execute("SELECT * FROM requests WHERE id=?",(rid,)).fetchone()
    if not r:
        con.close(); flash("ไม่พบรายการ","error"); return redirect(url_for("home"))
    if r["status"]!="รอตรวจสอบ":
        con.close(); flash("รายการนี้ดำเนินการไปแล้ว","error"); return redirect(url_for("home"))
    m=con.execute("SELECT * FROM materials WHERE id=?",(r["material_id"],)).fetchone()
    if r["qty"] > m["stock"]:
        con.close(); flash("สต็อกไม่เพียงพอ","error"); return redirect(url_for("home"))
    con.execute("UPDATE materials SET stock=stock-? WHERE id=?",(r["qty"],m["id"]))
    con.execute("UPDATE requests SET status='จ่ายของแล้ว',approved_at=? WHERE id=?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),rid))
    con.commit(); con.close()
    flash("อนุมัติและตัดสต็อกแล้ว","success"); return redirect(url_for("home"))

@app.post("/reject/<int:rid>")
@admin_required
def reject(rid):
    con=db(); con.execute("UPDATE requests SET status='ไม่อนุมัติ',approved_at=? WHERE id=?",
                           (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),rid))
    con.commit(); con.close(); flash("บันทึกไม่อนุมัติแล้ว","success"); return redirect(url_for("home"))

@app.post("/material/add")
@admin_required
def add_material():
    code=request.form["code"].strip(); name=request.form["name"].strip()
    unit=request.form.get("unit","ชิ้น").strip(); stock=int(request.form.get("stock",0))
    con=db()
    try:
        con.execute("INSERT INTO materials(code,name,unit,stock) VALUES(?,?,?,?)",(code,name,unit,stock))
        con.commit(); flash("เพิ่มวัสดุแล้ว","success")
    except sqlite3.IntegrityError: flash("รหัสวัสดุซ้ำ","error")
    con.close(); return redirect(url_for("home"))

@app.post("/material/<int:mid>/update")
@admin_required
def update_material(mid):
    code=request.form["code"].strip(); name=request.form["name"].strip()
    unit=request.form.get("unit","ชิ้น").strip(); stock=int(request.form.get("stock",0))
    con=db()
    try:
        con.execute("UPDATE materials SET code=?,name=?,unit=?,stock=? WHERE id=?",(code,name,unit,stock,mid))
        con.commit(); flash("แก้ไขวัสดุแล้ว","success")
    except sqlite3.IntegrityError: flash("รหัสวัสดุซ้ำ","error")
    con.close(); return redirect(url_for("home"))

@app.route("/export")
@admin_required
def export():
    con=db()
    rows=con.execute("""SELECT r.id,u.name requester,m.code,m.name material,r.qty,m.unit,
                        r.status,r.created_at,r.approved_at
                        FROM requests r JOIN users u ON u.id=r.user_id
                        JOIN materials m ON m.id=r.material_id ORDER BY r.id DESC""").fetchall()
    con.close()
    out=io.StringIO(); w=csv.writer(out)
    w.writerow(["เลขที่","ผู้เบิก","รหัสวัสดุ","รายการ","จำนวน","หน่วย","สถานะ","วันที่ขอ","วันที่จ่าย"])
    for x in rows: w.writerow(list(x))
    data=io.BytesIO(out.getvalue().encode("utf-8-sig")); data.seek(0)
    return send_file(data, mimetype="text/csv; charset=utf-8", as_attachment=True,
                     download_name="รายงานการเบิกวัสดุ.csv")

@app.route("/api/materials")
@login_required
def api_materials():
    q=request.args.get("q","").strip()
    con=db()
    rows=con.execute("SELECT * FROM materials WHERE name LIKE ? OR code LIKE ? ORDER BY name",
                     (f"%{q}%",f"%{q}%")).fetchall()
    con.close()
    return jsonify([dict(x) for x in rows])

if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
