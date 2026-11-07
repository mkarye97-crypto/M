#!/usr/bin/env python3
"""Summarize sanitized API audit logs without exposing credentials."""
import json
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parent
path = root / 'data' / 'api_traffic_audit.jsonl'
if not path.exists():
    print('لا يوجد سجل بعد. شغّل main.py أولاً.')
    raise SystemExit(0)
counts = Counter()
errors = []
question_paths = []
for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        continue
    key = f"{row.get('method')} {row.get('path')}"
    counts[(key, row.get('status'))] += 1
    if int(row.get('status') or 0) >= 400:
        errors.append((line_no, key, row.get('status'), row.get('response')))
    if '/test' in str(row.get('path')) or '/review' in str(row.get('path')):
        question_paths.append((line_no, key, row.get('status')))
print('=== QUREO API TRAFFIC SUMMARY ===')
for (key, status), count in sorted(counts.items(), key=lambda x: (x[0][0], x[0][1] or 0)):
    print(f'{count:4} x HTTP {status}: {key}')
print(f'\nطلبات اختبار/مراجعة مرصودة: {len(question_paths)}')
for item in question_paths:
    print('  ', item)
print(f'\nطلبات فاشلة: {len(errors)}')
for line_no, key, status, body in errors[-20:]:
    print(f'  line {line_no}: HTTP {status} {key} :: {str(body)[:300]}')
print('\nتنبيه: هذا ملخص اتصالات فقط، وليس إثباتًا لصحة إجابة.')
