#!/usr/bin/env python3
# MKARY QUREO Requests Assistant — lessons only, safe assessment mode
import argparse
import getpass
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import assessment_runner

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
API_BASE = "https://me-tp.qureo.education"
PORTAL_URL = "https://me-portal.qureo.education"
COURSE_CODE = "javascript_ar"
SECTION_ID = 5
DEFAULT_PAUSE = 0.35
TIMEOUT = 30


def save_json(name, obj):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = DATA_DIR / name
    target.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_excerpt(response):
    """Return a short error body without echoing credential-like values."""
    text = response.text[:500]
    text = re.sub(r'(?i)(authorization|access_token|refresh_token|id_token|password|cookie)("?\s*[:=]\s*"?)[^",\s}]+', r'\1\2[REDACTED]', text)
    return text


def prompt_credentials():
    print("\n=== MKARY QUREO | تسجيل الدخول ===")
    login_id = input("Login ID: ").strip()
    password = getpass.getpass("Password: ")
    if not login_id or not password:
        raise RuntimeError("لازم تدخل Login ID وكلمة المرور.")
    return login_id, password


def login(session, login_id=None, password=None):
    if login_id is None or password is None:
        login_id, password = prompt_credentials()
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
    session.headers.update({"User-Agent": ua, "Accept-Language": "ar,en-US;q=0.8,en;q=0.7"})
    landing = session.get(PORTAL_URL + "/", timeout=TIMEOUT)
    landing.raise_for_status()
    response = session.post(
        PORTAL_URL + "/oauth2/password-login",
        json={"student_id": login_id, "password": password},
        headers={"Origin": PORTAL_URL, "Referer": PORTAL_URL + "/", "Content-Type": "application/json"},
        allow_redirects=False,
        timeout=TIMEOUT,
    )
    if response.status_code not in (200, 201, 202, 204, 302, 303, 307, 308):
        raise RuntimeError(f"فشل تسجيل الدخول: HTTP {response.status_code}: {safe_excerpt(response)}")

    # Validate against study host. Portal /userinfo may return an HTML app shell.
    headers = {"Origin": API_BASE, "Referer": API_BASE + "/course/" + COURSE_CODE, "Accept": "application/json"}
    errors = []
    for path in ("/oauth2/userinfo", "/api/study/students/me"):
        try:
            r = session.get(API_BASE + path, headers=headers, timeout=TIMEOUT)
            if r.status_code != 200:
                errors.append(f"{path}: HTTP {r.status_code}")
                continue
            data = r.json()
            if isinstance(data, (dict, list)) and data:
                print("[LOGIN] تم تأكيد الجلسة من خادم الدراسة.")
                return data
            errors.append(f"{path}: استجابة JSON فارغة")
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{path}: {type(exc).__name__}")
    raise RuntimeError("الدخول لم يُؤكَّد من خادم الدراسة. " + " | ".join(errors) + " — لا ترسل Cookies أو Tokens.")


def redact(value, key=""):
    """Redact secrets recursively before writing diagnostic traffic."""
    secret = re.compile(r"token|authorization|cookie|password|secret|student_id|email", re.I)
    if secret.search(str(key)):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)bearer\s+[a-z0-9._~-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)(access_token|refresh_token|id_token|authorization|cookie)(\s*[:=]\s*)[^,;\s]+", r"\1\2[REDACTED]", value)
    return value


def audit_api(method, path, payload, response, body):
    """Record QUREO API behavior for debugging; never records auth endpoints or headers."""
    if path.startswith("/oauth2/") or path.endswith("/students/me"):
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = DATA_DIR / "api_traffic_audit.jsonl"
    record = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": method,
        "path": path,
        "request": redact(payload),
        "status": response.status_code,
        "response": redact(body),
    }
    # Bound log size to avoid uncontrolled growth.
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    if target.exists() and target.stat().st_size > 8 * 1024 * 1024:
        target.rename(DATA_DIR / "api_traffic_audit.previous.jsonl")
    with target.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def api(session, method, path, payload=None, expected=(200, 201, 204), retries=2):
    url = API_BASE + path
    headers = {"Accept": "application/json", "Origin": API_BASE, "Referer": API_BASE + "/course/" + COURSE_CODE}
    last_error = None
    for attempt in range(retries + 1):
        try:
            if method == "GET":
                r = session.get(url, headers=headers, timeout=TIMEOUT)
            elif method == "PUT":
                # Mutations are deliberately not retried to avoid duplicate writes.
                r = session.put(url, json=payload, headers=headers, timeout=TIMEOUT)
            else:
                raise ValueError("Unsupported HTTP method: " + method)
            if r.status_code in (401, 403):
                raise RuntimeError(f"{method} {path}: HTTP {r.status_code} — الجلسة غير صالحة.")
            try:
                body = r.json() if r.content else None
            except ValueError:
                body = r.text[:2000]
            audit_api(method, path, payload, r, body)
            if r.status_code not in expected:
                raise RuntimeError(f"{method} {path}: HTTP {r.status_code}: {safe_excerpt(r)}")
            return {"status": r.status_code, "body": body}
        except requests.RequestException as exc:
            last_error = exc
            if method != "GET" or attempt >= retries:
                break
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"تعذر تنفيذ {method} {path}: {last_error}")


def as_list(data, *keys):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
        for key in ("data", "result", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = as_list(value, *keys)
                if nested:
                    return nested
    return []


def is_completed(row):
    if not isinstance(row, dict):
        return False
    if row.get("completed_at"):
        return True
    status = str(row.get("status", "")).lower()
    return status in {"completed", "complete", "done", "finished"}


def id_of(row, *keys):
    if not isinstance(row, dict):
        return None
    for key in keys:
        value = row.get(key)
        if value is not None:
            return str(value)
    return None


def fetch_chapter_states(session):
    progress = api(session, "GET", f"/api/study/students/sections/{SECTION_ID}/chapters")["body"]
    save_json("runtime_progress.json", progress)
    return as_list(progress, "chapters", "progress")


def fetch_chapters(session):
    section = api(session, "GET", f"/api/study/sections/{SECTION_ID}")["body"]
    states = fetch_chapter_states(session)
    save_json("runtime_section.json", section)
    chapters = as_list(section, "chapters")
    if not chapters:
        raise RuntimeError("رد القسم لا يحتوي على قائمة chapters متوقعة؛ تم إيقاف التشغيل بدل تخمين الفصول.")
    return sorted(chapters, key=lambda x: int(x.get("seq", 0))), states


def chapter_state_summary(state):
    if not isinstance(state, dict):
        return {"state": "missing"}
    # Keep only non-sensitive progression fields; never dump IDs/tokens or arbitrary response data.
    keys = ("status", "completed", "is_completed", "completed_at", "test_completed", "review_completed", "progress")
    return {key: state[key] for key in keys if key in state}


def run_chapter_lectures(session, chapter):
    chapter_id = str(chapter.get("id") or chapter.get("chapter_id") or "")
    if not chapter_id:
        raise RuntimeError("فصل بدون chapter_id؛ إيقاف آمن.")
    chapter_data = api(session, "GET", f"/api/study/chapters/{chapter_id}")["body"]
    lectures = as_list(chapter_data, "lectures")
    state_data = api(session, "GET", f"/api/study/students/chapters/{chapter_id}/lectures")["body"]
    states = as_list(state_data, "lectures", "lecture_progress", "progress")
    completed = {id_of(x, "lecture_id", "id") for x in states if is_completed(x)}
    completed.discard(None)
    print(f"\n[CHAPTER] {chapter_id} | {chapter.get('name', 'بدون اسم')} | محاضرات: {len(lectures)}")
    for lecture in lectures:
        lecture_id = id_of(lecture, "id", "lecture_id")
        if not lecture_id:
            print("[SKIP] محاضرة بدون ID.")
            continue
        if lecture_id in completed:
            print(f"[SKIP-DONE] {lecture_id}")
            continue
        print(f"[START] {lecture_id}")
        api(session, "PUT", f"/api/study/students/lectures/{lecture_id}")
        details = api(session, "GET", f"/api/study/lectures/{lecture_id}")["body"]
        talks = as_list(details, "talks")
        for talk in talks:
            # Only replay an explicit value supplied by the live API. Never invent one.
            if isinstance(talk, dict) and talk.get("id") is not None and "value" in talk:
                api(session, "PUT", f"/api/study/students/lecture-talks/{talk['id']}", {"value": talk["value"]})
                time.sleep(DEFAULT_PAUSE)
        api(session, "PUT", f"/api/study/students/lectures/{lecture_id}/complete")
        save_json(f"lecture_{lecture_id}_details.json", details)
        print(f"[COMPLETE-SENT] {lecture_id}")
        time.sleep(DEFAULT_PAUSE)

    verify_data = api(session, "GET", f"/api/study/students/chapters/{chapter_id}/lectures")["body"]
    verified = as_list(verify_data, "lectures", "lecture_progress", "progress")
    save_json(f"chapter_{chapter_id}_lecture_status.json", verify_data)
    remaining = []
    for lec in lectures:
        lid = id_of(lec, "id", "lecture_id")
        row = next((x for x in verified if id_of(x, "lecture_id", "id") == lid), None)
        if row is None or not is_completed(row):
            remaining.append(lid)
    if remaining:
        print(f"[VERIFY-WARN] المنصة لم تؤكد اكتمال: {', '.join(remaining)}")
        return False
    print(f"[VERIFIED] كل محاضرات الفصل {chapter_id} مكتملة حسب API.")
    return True


def main():
    parser = argparse.ArgumentParser(description="MKARY QUREO Requests — إكمال المحاضرات فقط")
    parser.add_argument("--from-level", type=int, default=1)
    parser.add_argument("--to-level", type=int, default=42)
    parser.add_argument("--preview-only", action="store_true", help="اعرض المطابقات بدون إرسال إجابات")
    parser.add_argument("--retake", action="store_true", help="اطلب إعادة الاختبارات المكتملة لرفع الدرجة؛ قد يرفض الخادم إعادة المحاولة")
    args = parser.parse_args()
    if not (1 <= args.from_level <= args.to_level <= 42):
        parser.error("المستويات يجب أن تكون بين 1 و42، والبداية لا تتجاوز النهاية.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.retake and args.preview_only:
        parser.error("لا يمكن الجمع بين --retake و --preview-only")
    if not args.preview_only:
        if args.retake:
            print("\n[WARNING] وضع إعادة الاختبار: سيُنفذ نفس طلب زر «ابدأ من البداية» لكل فصل محدد، ثم يتحقق من حالة الطالب قبل إرسال الإجابات.")
            print("[WARNING] استجابة 204 لا تثبت وحدها تصفير المحاولة؛ الأداة تحفظ حالة ما بعد البدء وتتوقف إذا لم يؤكد الخادم وجود تقييم نشط.")
            confirm = input("اكتب RETAKE للتأكيد أو أي شيء للإلغاء: ").strip()
            if confirm != "RETAKE":
                print("[CANCEL] لم يتم طلب إعادة أي اختبار.")
                return 0
        else:
            print("\n[WARNING] سيتم بدء تقييمات الفصول وإرسال الإجابات المطابقة لمعرف السؤال من قاعدة البيانات.")
            print("[NOTE] قاعدة الإجابات مصدر خارجي غير رسمي؛ المنصة وحدها تؤكد صحة النتيجة.")
            confirm = input("اكتب SUBMIT للمتابعة أو أي شيء للإلغاء: ").strip()
            if confirm != "SUBMIT":
                print("[CANCEL] لم يتم إرسال أي إجابة.")
                return 0
    session = requests.Session()
    try:
        user = login(session)
        save_json("student_session_check.json", {"authenticated": True, "response_type": type(user).__name__})
        me = api(session, "GET", "/api/study/students/me")["body"]
        save_json("student_me_runtime.json", me)
        chapters, states = fetch_chapters(session)
        state_by_id = {id_of(x, "chapter_id", "id"): x for x in states if id_of(x, "chapter_id", "id")}
        selected = [c for c in chapters if args.from_level <= int(c.get("seq", 0)) <= args.to_level]
        if not selected:
            raise RuntimeError("لم يتم العثور على فصول في النطاق المحدد.")
        results = []
        db = assessment_runner.load_db()
        print(f"[DB] تم تحميل {len(db)} سؤالًا بمفاتيح الفصل/السؤال.")
        for chapter in selected:
            cid = id_of(chapter, "id", "chapter_id")
            state = state_by_id.get(cid, {})
            chapter_was_completed = is_completed(state)
            if chapter_was_completed and not args.retake:
                print(f"[CHAPTER-DONE] {cid} — تم تخطي الفصل المكتمل.")
                results.append({"chapter_id": cid, "status": "already_completed"})
                continue
            if chapter_was_completed and args.retake:
                print(f"[RETAKE] {cid} — الفصل مكتمل؛ سيتم طلب محاولة جديدة صراحةً.")
            if not args.preview_only:
                ok = run_chapter_lectures(session, chapter)
                if not ok:
                    results.append({"chapter_id": cid, "status": "needs_review"})
                    print("[STOP] لم يتم تأكيد كل المحاضرات؛ توقفت قبل الفصل التالي.")
                    break
            else:
                print(f"[PREVIEW] {cid}: فحص الأسئلة فقط؛ لن يتم بدء الاختبار أو تعديل المحاضرات.")

            # Complete the chapter assessment/review using exact question IDs in the local DB.
            try:
                assessment_result = assessment_runner.solve_assessment(
                    session, chapter, db, submit=not args.preview_only, retake=args.retake
                )
                results.append({"chapter_id": cid, "assessment": assessment_result})
            except Exception as exc:
                print(f"[ASSESSMENT-ERROR] {cid}: {exc}")
                results.append({"chapter_id": cid, "status": "assessment_error", "error": str(exc)})
                print("[STOP] توقفت عند التقييم حتى لا يتم تجاوز شرط الفصل.")
                break

            if args.preview_only:
                continue

            # Refresh server-side chapter progression after the assessment.
            fresh_states = fetch_chapter_states(session)
            fresh_by_id = {id_of(x, "chapter_id", "id"): x for x in fresh_states if id_of(x, "chapter_id", "id")}
            fresh_state = fresh_by_id.get(cid)
            if not is_completed(fresh_state):
                summary = chapter_state_summary(fresh_state)
                save_json(f"chapter_{cid}_gate_status.json", summary)
                results.append({"chapter_id": cid, "status": "lectures_done_chapter_gate_pending", "state": summary})
                print(f"[CHAPTER-GATE] محاضرات {cid} مكتملة، لكن حالة الفصل نفسها غير مكتملة على الخادم.")
                print("[STOP] لن أحاول الفصل التالي حتى تستوفي شرط QUREO للفصل الحالي (قد يكون تقييمًا/مراجعة أو إجراء إكمال آخر).")
                print(f"[INFO] ملخص حالة الفصل محفوظ في data/chapter_{cid}_gate_status.json")
                break

            results.append({"chapter_id": cid, "status": "chapter_verified_completed"})
        save_json("run_report.json", results)
        print("\n[FINISHED] انتهى التشغيل. راجع data/run_report.json وملفات assessment_*.json.")
        print("[NOTICE] الإجابات غير المطابقة لمعرف سؤال دقيق تُترك دون تخمين. راجع نتائج الخادم لكل فصل.")
    except KeyboardInterrupt:
        print("\n[STOP] أوقف المستخدم التشغيل.")
        return 130
    except Exception as exc:
        print("[ERROR]", exc)
        return 1
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
