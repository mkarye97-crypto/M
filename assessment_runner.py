# -*- coding: utf-8 -*-
"""QUREO assessment runner. Uses exact question IDs from the bundled local DB.
Answers are suggestions from an unofficial source; every chapter result is checked
against QUREO. Unknown/ambiguous questions are left unanswered, never guessed.
"""
import json, re, time, unicodedata
from pathlib import Path
import main as core

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "question_answer_database.json"
LEDGER_PATH = ROOT / "data" / "assessment_ledger.json"

def load_ledger():
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

def save_ledger(ledger):
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LEDGER_PATH)

def state_has_open_attempt(payload):
    """Detect an existing attempt so the start endpoint is never called twice."""
    found = []
    def walk(node):
        if isinstance(node, dict):
            if node.get("work_in_process") is True or node.get("in_progress") is True:
                found.append(True)
            if node.get("started_at") or node.get("startedAt"):
                found.append(True)
            status = str(node.get("status", "")).lower()
            if status in {"started", "in_progress", "in-progress", "working"}:
                found.append(True)
            for value in node.values(): walk(value)
        elif isinstance(node, list):
            for value in node: walk(value)
    walk(payload)
    return bool(found)

def result_is_final(payload):
    """Conservative detector: only explicit final/result flags count as finished."""
    found = []
    def walk(node):
        if isinstance(node, dict):
            for key in ("visible_result", "is_completed", "completed", "review_completed", "test_completed"):
                if node.get(key) is True:
                    found.append(True)
            status = str(node.get("status", "")).lower()
            if status in {"completed", "complete", "finished", "passed"}:
                found.append(True)
            if node.get("completed_at"):
                found.append(True)
            for value in node.values(): walk(value)
        elif isinstance(node, list):
            for value in node: walk(value)
    walk(payload)
    return bool(found)


def norm(s):
    s = str(s or "").lower()
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\[\[/?(?:bdi|bold|italic|underline|copy)[^\]]*\]\]", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\u0600-\u06ff]+", " ", s)).strip()


def write(name, value):
    core.save_json(name, value)


def load_db():
    obj = json.loads(DB_PATH.read_text(encoding="utf-8"))
    return {(str(x.get("chapter_id")), str(x.get("question_id"))): x
            for x in obj.get("questions", [])}


def parse_questions(payload):
    """HAR-confirmed shape: [{question:{id,content,type},choices,descriptions}]."""
    if isinstance(payload, dict):
        for k in ("questions", "data", "items", "result"):
            if isinstance(payload.get(k), list):
                payload = payload[k]
                break
    out = []
    if not isinstance(payload, list):
        return out
    for row in payload:
        if not isinstance(row, dict):
            continue
        q = row.get("question") if isinstance(row.get("question"), dict) else row
        qid = q.get("id") or q.get("question_id") or row.get("question_id")
        if qid is None:
            continue
        choices = row.get("choices") or []
        descriptions = row.get("descriptions") or []
        out.append({
            "id": str(qid), "type": str(q.get("type") or ("choice" if choices else "description")),
            "content": q.get("content") or q.get("text") or "",
            "hints": q.get("hints") or [], "choices": choices,
            "descriptions": descriptions, "raw": row,
        })
    return out


def extract_description_answer(dbrow):
    """Extract only explicit answer fragments from a noisy reference page.

    Supports the source wording used by Level 8 ("الإجابة نكتب في المكان
    الفارغ") as well as earlier wording. It never falls back to the whole page.
    """
    raw = str(dbrow.get("suggested_answer") or "").strip()
    if not raw:
        return None

    # Single missing slot: accept the first standalone code/expression line
    # after a clearly labeled answer instruction.
    single_markers = (
        r"(?:الإجابة|الاجابه)\s*نكتب\s+في\s+(?:المكان\s+الفارغ|الفراغ|النواقص)",
        r"(?:نكتب|اكتب|أكتب|ضع|أدخل)\s+في\s+النواقص",
        r"(?:الإجابة|الاجابه)\s*[:：]",
    )
    for marker in single_markers:
        m = re.search(marker + r"\s*[:：]?\s*\n+([\s\S]*?)(?=\n\s*(?:ليصبح|شكل الكود النهائي|ليكون الكود|الف مبروك|لو محتاج|حل المستوى)|$)", raw, re.I)
        if m:
            lines = [x.strip().strip('`') for x in m.group(1).splitlines() if x.strip()]
            if lines:
                candidate = lines[0].rstrip(";")
                if len(candidate) <= 2000 and not re.search(r"[\u0600-\u06ff]|https?://", candidate):
                    return candidate

    # Explicit ordered fill-in list. Return newline-separated fragments only
    # when the source clearly labels the list and every extracted line is code.
    m = re.search(r"نكتب\s+النواقص\s+في\s+الاسطر\s+هكذا\s+بالترتيب\s*[:：]?\s*\n+([\s\S]*?)(?=\n\s*(?:ليصبح|شكل الكود النهائي|الف مبروك|لو محتاج|حل المستوى)|$)", raw, re.I)
    if m:
        lines = [x.strip().strip('`') for x in m.group(1).splitlines() if x.strip()]
        if lines and len(lines) <= 10 and all(
            len(x) <= 500 and not re.search(r"[\u0600-\u06ff]|https?://", x)
            for x in lines
        ):
            return "\n".join(x.rstrip(";") for x in lines)

    # A lone explicit else-if line is a safe compact fragment.
    candidates = [x.strip().strip('`').rstrip(';') for x in raw.splitlines()
                  if re.search(r"^else\s+if\s*\(", x.strip(), re.I)]
    if len(candidates) == 1 and len(candidates[0]) <= 2000:
        return candidates[0]
    return None


def response_completion(response_body):
    if not isinstance(response_body, dict):
        return None, None, False
    result = response_body.get("result") if isinstance(response_body.get("result"), dict) else {}
    complete = response_body.get("complete") if isinstance(response_body.get("complete"), dict) else {}
    chapter = complete.get("chapter") if isinstance(complete.get("chapter"), dict) else {}
    correct = result.get("correct") if isinstance(result.get("correct"), bool) else None
    return correct, result.get("model_answer"), bool(chapter.get("completed_at"))


def live_description_code(q):
    """Return the editable starter code supplied by QUREO for this live question."""
    descriptions = q.get("descriptions") or []
    codes = [str(x.get("code") or "") for x in descriptions if isinstance(x, dict)]
    return codes[0] if len(codes) == 1 else None


def exact_description_solution(q, dbrow):
    """Build full editor contents for the seven description questions in levels 1-7.

    The question ID is not used alone: prompt tokens and the live starter code are
    checked as a guard against a changed/recycled question ID. The returned value
    is the complete editor buffer, not merely the inserted fragment.
    """
    qid = str(q.get("id"))
    content = norm(q.get("content", ""))
    starter = live_description_code(q)
    expected = {
        "90001020": (('good morning', 'وحدة التحكم'), 'console.log', 'console.log("Good morning");'),
        "90002030": (('19', '40', 'نتيجة الحساب'), 'console.log();', 'console.log(19+40);'),
        "90003030": (('number', 'أعلن'), '', 'let number;'),
        "90004020": (('hello', 'console'), '', 'console.log("Hello");'),
        "90005010": (('num', '5', 'سطرين'), '', 'let num = 5;\\nconsole.log(num);'),
        "90005040": (('num', '100', 'correct'), '', 'let num = 100;\\nif (num == 100){console.log("Correct");}'),
        "90006020": (('num', '10', 'win', 'lose'), '', 'let num = 5;\\nif (num == 10){console.log("Win");}\\nelse {console.log("Lose");}'),
        "90007020": (('num', '2', 'two', 'السطر الثالث'), 'let num = 2;\\nif (num == 1){console.log("One");}', None),
    }
    spec = expected.get(qid)
    if not spec or not all(norm(token) in content for token in spec[0]):
        return None
    expected_starter, answer = spec[1], spec[2]
    if starter is None:
        return None
    if expected_starter:
        expected_starter = expected_starter.replace("\\n", "\n")
    if expected_starter and starter.strip() != expected_starter:
        return None
    if qid == "90007020":
        if starter.strip() != expected_starter:
            return None
        answer = starter.rstrip() + '\nelse if (num == 2){console.log("Two");}'
    elif starter.strip() not in ("", expected_starter):
        # QUREO changed the prefilled editor: don't overwrite unknown user code.
        return None
    if not answer or len(answer) > 2000:
        return None
    return answer.replace('\\n', '\n')


def pick_answer(q, dbrow):
    if not dbrow:
        return None, "question_id_not_in_database"
    answer = dbrow.get("suggested_answer")
    if not answer:
        return None, "database_has_no_answer"
    qtype = q["type"].lower()
    if qtype == "choice":
        # QUREO may shuffle/reorder options between attempts. A database choice
        # ID or A/B/C/D position is not a stable answer key. Resolve the intended
        # option by its full visible content against THIS live question, then
        # send the live choice ID returned by the server.
        choices = q["choices"]
        def choice_key(value):
            value = unicodedata.normalize("NFKC", str(value or ""))
            value = re.sub(r"\[\[/?(?:bdi|bold|italic|underline|copy)[^\]]*\]\]", "", value)
            value = re.sub(r"<[^>]+>", "", value)
            return re.sub(r"\s+", " ", value).strip().casefold()

        target = choice_key(dbrow.get("matched_choice") or answer)
        exact = [c for c in choices if target and choice_key(c.get("content")) == target]
        if len(exact) == 1 and exact[0].get("id") is not None:
            return {"kind": "choice", "choice_id": int(exact[0]["id"]), "answer": answer,
                    "source_status": dbrow.get("source_verification")}, "live_choice_text_match"
        return None, "choice_not_mapped_uniquely_by_live_text"
    # Description answers must come from exact question ID. For noisy scraped
    # sources, extract the missing-slot fragment instead of sending the whole page.
    if qtype == "description":
        # Prefer a live-question-specific solution when QUREO exposes the starter
        # editor buffer. This covers the known level 1-7 descriptions whose
        # third-party answer text is explanatory prose rather than raw code.
        answer_code = exact_description_solution(q, dbrow)
        answer_reason = "live_prompt_and_starter_code_template" if answer_code else None
        if not answer_code:
            answer_code = extract_description_answer(dbrow)
            answer_reason = "exact_question_id"
        if not answer_code:
            return None, "description_answer_not_safely_extractable"
        if len(answer_code) > 2000:
            return None, "description_answer_exceeds_2000_chars"
        return {"kind": "description", "answer_code": answer_code,
                "source_status": dbrow.get("source_verification")}, answer_reason
    return None, "unsupported_question_type"


def should_skip_prior_answer(question_id, answered_ids, retake=False):
    """Only skip ledger entries when resuming the same attempt, never on retake."""
    return (not retake) and str(question_id) in set(map(str, answered_ids or []))


def solve_assessment(session, chapter, db, submit=True, retake=False):
    cid = str(chapter.get("id") or chapter.get("chapter_id"))
    level = int(chapter.get("seq") or 0)
    test_type = str(chapter.get("test_type") or "").lower()
    if not test_type:
        meta = core.api(session, "GET", f"/api/study/chapters/{cid}")["body"]
        def find_type(node):
            if isinstance(node, dict):
                if node.get("test_type"):
                    return str(node["test_type"]).lower()
                for value in node.values():
                    found = find_type(value)
                    if found:
                        return found
            elif isinstance(node, list):
                for value in node:
                    found = find_type(value)
                    if found:
                        return found
            return ""
        test_type = find_type(meta) or "test"
    # Check server-side result and persistent local ledger before opening anything.
    ledger = load_ledger()
    entry = ledger.setdefault(cid, {"started": False, "finalized": False, "answered_ids": []})
    if retake and submit:
        # Preserve prior attempt metadata; a new attempt is only requested by explicit --retake.
        history = entry.setdefault("retake_history", [])
        history.append({"previous_answered_ids": list(entry.get("answered_ids", [])),
                        "previous_finalized": bool(entry.get("finalized")),
                        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        # Do not erase prior answer IDs: the observed API state only exposes
        # work_in_process and visible_result, which cannot prove a fresh attempt.
        # Keep prior IDs as a safety barrier until a fresh attempt is independently verified.
        entry["retake_reset_verified"] = False
        save_ledger(ledger)
    for suffix in (() if retake else ("test/result", "review/result")):
        try:
            prior = core.api(session, "GET", f"/api/study/students/chapters/{cid}/{suffix}")["body"]
            write(f"assessment_{cid}_prior_{suffix.replace('/', '_')}.json", prior)
            if result_is_final(prior):
                entry["finalized"] = True
                entry["server_final_result"] = True
                save_ledger(ledger)
                print(f"[ASSESSMENT-SKIP] {cid}: نتيجة نهائية موجودة على الخادم؛ لن يُفتح التقييم مرة أخرى.")
                return {"chapter_id": cid, "status": "already_finalized_server", "submitted": 0}
        except Exception:
            pass
    if entry.get("finalized") and not retake:
        print(f"[ASSESSMENT-SKIP] {cid}: مسجل كمكتمل محليًا؛ لن يُفتح مرة أخرى.")
        return {"chapter_id": cid, "status": "already_finalized_local", "submitted": 0}

    # Recover an in-progress attempt from QUREO before deciding whether to start.
    if not retake and not entry.get("started"):
        try:
            student_state = core.api(session, "GET", f"/api/study/students/chapters/{cid}/test")["body"]
            write(f"assessment_{cid}_student_state.json", student_state)
            if state_has_open_attempt(student_state):
                entry["started"] = True
                save_ledger(ledger)
                print(f"[ASSESSMENT-RESUME] {cid}: الخادم يؤكد وجود محاولة مفتوحة؛ لن نرسل طلب بدء جديد.")
        except Exception:
            pass

    # Chapter test metadata is read first; no assumed schema beyond observed fields.
    test_payload = core.api(session, "GET", f"/api/study/chapters/{cid}/test")["body"]
    questions = parse_questions(test_payload)
    if not questions:
        write(f"assessment_{cid}_unparsed.json", test_payload)
        print(f"[ASSESSMENT-STOP] {cid}: لم أتعرف على شكل الأسئلة؛ لم يتم إرسال أي إجابة.")
        return {"chapter_id": cid, "status": "unparsed_questions", "questions": 0}

    route_kind = "review" if test_type == "review" else "test"
    endpoint_plural = "review-questions" if route_kind == "review" else "test-questions"
    # Start/initialize only in submit mode after explicit confirmation.
    if submit and (retake or not entry.get("started")):
        # Reproduce the browser's observed "start from beginning" action:
        # PUT /students/chapters/{id}/test, then GET student test state.
        # A 204 alone is NOT proof of a fresh attempt.
        started = core.api(session, "PUT", f"/api/study/students/chapters/{cid}/test")
        entry["started"] = True
        entry["last_start_status"] = started.get("status")
        entry["last_start_mode"] = "browser_start_from_beginning" if retake else "first_attempt"
        entry["start_requested_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_ledger(ledger)
        time.sleep(core.DEFAULT_PAUSE)
        try:
            post_start_state = core.api(session, "GET", f"/api/study/students/chapters/{cid}/test")["body"]
            write(f"assessment_{cid}_student_state_after_start.json", post_start_state)
            active = state_has_open_attempt(post_start_state)
            entry["post_start_state"] = post_start_state
            entry["post_start_active"] = active
            save_ledger(ledger)
            if not active:
                print(f"[RETAKE-STOP] {cid}: طلب البدء رجع {started.get('status')} لكن الخادم لم يؤكد work_in_process؛ لن تُرسل إجابات.")
                return {"chapter_id": cid, "status": "start_not_verified", "submitted": 0,
                        "retake_requested": bool(retake)}
            if retake:
                print(f"[RETAKE-ACTIVE] {cid}: نفّذنا نفس طلب زر ابدأ من البداية، والخادم يؤكد work_in_process=true.")
                print("[RETAKE-LIMIT] حالة API لا تميّز وحدها محاولة جديدة عن محاولة قديمة؛ لا يوجد تأكيد مستقل لتصفير التقدم.")
            else:
                print(f"[ASSESSMENT-STARTED] {cid}: الخادم يؤكد وجود تقييم نشط.")
        except Exception as exc:
            print(f"[ASSESSMENT-STOP] {cid}: تعذر التحقق من حالة التقييم بعد PUT: {exc}")
            return {"chapter_id": cid, "status": "post_start_state_unverified", "submitted": 0,
                    "retake_requested": bool(retake)}
    elif submit:
        print(f"[ASSESSMENT-RESUME] {cid}: استكمال المحاولة الحالية بدون إعادة فتح التقييم.")

    details, unresolved, sent = [], [], []
    for q in questions:
        dbrow = db.get((cid, q["id"]))
        answer, reason = pick_answer(q, dbrow)
        record = {"question_id": q["id"], "question": q["content"], "type": q["type"],
                  "match": reason, "answer_source": (dbrow or {}).get("answer_source"),
                  "source_verification": (dbrow or {}).get("source_verification")}
        if not answer:
            unresolved.append(record)
            details.append(record)
            print(f"[UNRESOLVED] Q{q['id']} — {reason}")
            print("[SEQUENCE-STOP] لن يتم إرسال أي سؤال لاحق قبل حل هذا السؤال؛ QUREO يشترط الإجابة بالترتيب.")
            break
        if not submit:
            record["proposed_answer"] = answer
            details.append(record)
            continue
        # A prior accepted answer belongs to the previous attempt. During an
        # explicit retake, never skip it: QUREO enforces question order within
        # the active attempt. Keep the old IDs in the ledger for audit/history.
        if should_skip_prior_answer(q["id"], entry.get("answered_ids", []), retake=retake):
            record["skipped"] = "already_accepted_in_previous_run"
            details.append(record)
            print(f"[ANSWER-SKIP] Q{q['id']} — تم قبول إرسالها سابقًا؛ لن تتكرر.")
            continue
        if answer["kind"] == "choice":
            path = f"/api/study/students/{endpoint_plural}/{q['id']}/answer/choice"
            payload = {"choice_id": answer["choice_id"]}
        else:
            path = f"/api/study/students/{endpoint_plural}/{q['id']}/answer/description"
            code = answer["answer_code"]
            if len(code) > 2000:
                unresolved.append({**record, "reason": "answer_code_over_2000_chars", "length": len(code)})
                print(f"[UNRESOLVED] Q{q['id']} — answer_code أطول من 2000 حرف")
                details.append(record)
                continue
            payload = {"answer_code": code}
            record["answer_code_length"] = len(code)
        try:
            result = core.api(session, "PUT", path, payload)
            record["submitted"] = True
            record["http_status"] = result["status"]
            correct, model_answer, chapter_completed = response_completion(result.get("body"))
            if correct is not None:
                record["server_correct"] = correct
            if model_answer:
                record["server_model_answer"] = model_answer
            sent.append(record)
            entry.setdefault("answered_ids", []).append(q["id"])
            if chapter_completed:
                entry["finalized"] = True
                entry["server_final_result"] = True
                entry["completed_at_from_answer"] = True
            save_ledger(ledger)
            if correct is False:
                print(f"[ANSWER-WRONG] Q{q['id']}: الخادم أكد أن الإجابة خاطئة؛ سيتوقف الفصل فورًا.")
                details.append(record)
                write(f"assessment_{cid}_answers.json", details)
                break
            else:
                print(f"[ANSWER-SENT] Q{q['id']} ({reason})")
            if chapter_completed:
                print(f"[CHAPTER-COMPLETE] {cid}: الخادم أكد اكتمال الفصل في استجابة الإجابة.")
        except Exception as exc:
            record["submitted"] = False
            record["error"] = str(exc)
            unresolved.append(record)
            print(f"[ANSWER-ERROR] Q{q['id']}: {exc}")
            details.append(record)
            print("[SEQUENCE-STOP] تم إيقاف الفصل عند أول خطأ إرسال لتجنب أخطاء prev_question_not_answered.")
            break
        details.append(record)
        time.sleep(core.DEFAULT_PAUSE)

    write(f"assessment_{cid}_answers.json", details)
    if submit:
        # Do not repeat PUT /chapters/{id}/test after answers; it may change attempt state.
        if entry.get("finalized"):
            status = "server_completed_from_answer"
            correct_count = wrong_count = None
        else:
            print(f"[ASSESSMENT-INCOMPLETE] {cid}: لا يوجد تأكيد اكتمال من الخادم؛ لن نرسل طلب إنهاء إضافيًا أو نفتح محاولة جديدة.")
            result_payload = None
            result_errors = []
            for suffix in ("test/result", "review/result"):
                try:
                    result_payload = core.api(session, "GET", f"/api/study/students/chapters/{cid}/{suffix}")["body"]
                    break
                except Exception as exc:
                    result_errors.append(str(exc))
            if result_payload is not None:
                write(f"assessment_{cid}_server_result.json", result_payload)
                correctness = []
                def walk(x):
                    if isinstance(x, dict):
                        if isinstance(x.get("correct"), bool): correctness.append(x["correct"])
                        for v in x.values(): walk(v)
                    elif isinstance(x, list):
                        for v in x: walk(v)
                walk(result_payload)
                correct_count = sum(correctness)
                wrong_count = len(correctness) - correct_count
                status = "server_result_verified" if correctness else "result_saved_unparsed"
            else:
                status = "submitted_result_unavailable"
                correct_count = wrong_count = None
                write(f"assessment_{cid}_result_errors.json", result_errors)
    else:
        status, correct_count, wrong_count = "preview_only", None, None
    return {"chapter_id": cid, "level": level, "test_type": test_type, "status": status,
            "retake_requested": bool(retake),
            "questions": len(questions), "submitted": len(sent), "unresolved": len(unresolved),
            "correct": correct_count, "wrong": wrong_count,
            "finalized": bool(entry.get("finalized")), "answered_ids": entry.get("answered_ids", [])}
