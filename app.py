# -*- coding: utf-8 -*-
import os, uuid, threading, time, traceback
from flask import Flask, render_template, request, jsonify, session as web_session
import requests
import main as core
import assessment_runner

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key or len(app.secret_key) < 32:
    if os.environ.get("PORT") or os.environ.get("RAILWAY_ENVIRONMENT"):
        raise RuntimeError("Set SECRET_KEY in Railway variables (at least 32 characters).")
    app.secret_key = "local-dev-only-change-this-secret-key-32"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("PORT") or os.environ.get("RAILWAY_ENVIRONMENT")),
    MAX_CONTENT_LENGTH=16 * 1024,
)

# One Railway worker is intentional: the active QUREO session and job state are in memory.
RUNTIME = {}
LOCK = threading.Lock()

def state():
    sid = web_session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        web_session["sid"] = sid
    with LOCK:
        return RUNTIME.setdefault(sid, {"qsession": None, "user": None, "job": {
            "running": False, "stage": "لم يبدأ", "logs": [], "results": []
        }})

def log(st, message):
    with LOCK:
        st["job"]["logs"].append({"time": time.strftime("%H:%M:%S"), "message": str(message)[:600]})
        st["job"]["logs"] = st["job"]["logs"][-250:]
        st["job"]["stage"] = str(message)[:160]

@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/health")
def health():
    return jsonify(status="ok")

@app.get("/api/status")
def status():
    st=state()
    return jsonify(authenticated=bool(st["qsession"]), user=st["user"],
                   job=st["job"])

@app.post("/api/login")
def do_login():
    st=state()
    body=request.get_json(silent=True) or {}
    student=(body.get("student_code") or "").strip()
    password=body.get("password") or ""
    if not student or not password:
        return jsonify(error="اكتب كود الطالب وكلمة السر."),400
    if len(student)>160 or len(password)>1024:
        return jsonify(error="البيانات المدخلة أطول من الحد المسموح."),400
    qsession=requests.Session()
    try:
        user=core.login(qsession, student, password)
        # Password is never retained; only the authenticated in-memory session remains.
        st["qsession"]=qsession
        if isinstance(user,dict):
            st["user"]={k:user.get(k) for k in ("name","student_name","display_name") if user.get(k)}
        else:
            st["user"]={}
        log(st,"تم تسجيل الدخول والتحقق من جلسة QUREO.")
        return jsonify(ok=True, message="تم تسجيل الدخول بنجاح.")
    except Exception as exc:
        qsession.close()
        return jsonify(error=str(exc)[:500]),400
    finally:
        password=None

@app.post("/api/logout")
def logout():
    st=state()
    if st.get("qsession"):
        st["qsession"].close()
    st["qsession"]=None
    st["user"]=None
    log(st,"تم تسجيل الخروج ومسح جلسة QUREO من الذاكرة.")
    return jsonify(ok=True)

def worker(st, start, end, preview):
    try:
        qsession=st["qsession"]
        if qsession is None:
            raise RuntimeError("سجّل الدخول أولًا.")
        log(st,"جاري قراءة بيانات الفصول من QUREO...")
        me=core.api(qsession,"GET","/api/study/students/me")["body"]
        chapters, states=core.fetch_chapters(qsession)
        state_by_id={core.id_of(x,"chapter_id","id"):x for x in states if core.id_of(x,"chapter_id","id")}
        selected=[c for c in chapters if start<=int(c.get("seq",0))<=end]
        if not selected:
            raise RuntimeError("لم يتم العثور على فصول في النطاق المحدد.")
        db=assessment_runner.load_db()
        results=[]
        log(st,f"تم العثور على {len(selected)} فصلًا، وقاعدة المطابقة تحتوي {len(db)} سجلًا.")
        for n,ch in enumerate(selected,1):
            cid=core.id_of(ch,"id","chapter_id")
            if core.is_completed(state_by_id.get(cid,{})):
                results.append({"chapter_id":cid,"status":"already_completed"})
                log(st,f"الفصل {ch.get('seq',cid)} مكتمل بالفعل؛ تم تخطيه.")
                continue
            if preview:
                log(st,f"معاينة الفصل {ch.get('seq',cid)} بدون إرسال أو تعديل.")
            else:
                log(st,f"جاري إكمال محاضرات الفصل {ch.get('seq',cid)}...")
                if not core.run_chapter_lectures(qsession,ch):
                    results.append({"chapter_id":cid,"status":"needs_review"})
                    log(st,"توقفت العملية لأن QUREO لم يؤكد اكتمال المحاضرات.")
                    break
            log(st,f"جاري فحص تقييم الفصل {ch.get('seq',cid)}...")
            result=assessment_runner.solve_assessment(qsession,ch,db,submit=not preview,retake=False)
            results.append({"chapter_id":cid,"assessment":result})
            if not preview:
                fresh=core.fetch_chapter_states(qsession)
                fresh_by_id={core.id_of(x,"chapter_id","id"):x for x in fresh if core.id_of(x,"chapter_id","id")}
                if not core.is_completed(fresh_by_id.get(cid)):
                    results.append({"chapter_id":cid,"status":"chapter_gate_pending"})
                    log(st,"المحاضرات/التقييمات أُرسلت، لكن QUREO لم يؤكد اكتمال الفصل؛ توقفت العملية.")
                    break
            log(st,f"انتهى الفصل {ch.get('seq',cid)}.")
        core.save_json("run_report.json",results)
        st["job"]["results"]=results
        log(st,"انتهى التشغيل. راجع ملخص النتائج أدناه.")
    except Exception as exc:
        log(st,"توقفت العملية: "+str(exc))
    finally:
        st["job"]["running"]=False

@app.post("/api/start")
def start():
    st=state()
    if not st["qsession"]:
        return jsonify(error="سجّل الدخول أولًا."),401
    if st["job"]["running"]:
        return jsonify(error="هناك عملية قيد التشغيل بالفعل."),409
    body=request.get_json(silent=True) or {}
    try:
        start=int(body.get("from_level",1)); end=int(body.get("to_level",42))
    except (TypeError,ValueError):
        return jsonify(error="نطاق المستويات غير صحيح."),400
    if not (1<=start<=end<=42):
        return jsonify(error="اختر نطاقًا من 1 إلى 42."),400
    preview=bool(body.get("preview",True))
    st["job"]={"running":True,"stage":"بدء التشغيل","logs":[],"results":[]}
    t=threading.Thread(target=worker,args=(st,start,end,preview),daemon=True)
    t.start()
    return jsonify(ok=True,message="بدأت العملية.")

@app.post("/api/stop")
def stop():
    # No unsafe forced thread termination; explain that current network request completes first.
    return jsonify(ok=False,message="لا يمكن إيقاف طلب جارٍ بأمان؛ انتظر انتهاء الطلب الحالي."),409

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
