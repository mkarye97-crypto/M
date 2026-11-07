#!/usr/bin/env python3
# Offline question matcher: never submits answers or guesses.
import json,re,html,argparse,unicodedata
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def norm(s):
 s=html.unescape(str(s or '')).lower()
 s=re.sub(r'\[\[/?(?:bdi|bold|/bold|/bdi)\]\]',' ',s)
 s=re.sub(r'<[^>]+>',' ',s)
 s=unicodedata.normalize('NFKC',s)
 s=re.sub(r'[^\w\u0600-\u06ff]+',' ',s)
 return ' '.join(s.split())
def main():
 p=argparse.ArgumentParser(description='مطابقة سؤال QUREO مع قاعدة البيانات المحلية')
 p.add_argument('--chapter',required=True);p.add_argument('--question',required=True);a=p.parse_args()
 bank=json.load(open(ROOT/'assessment_question_bank.json',encoding='utf-8'))
 target=norm(a.question); rows=[x for x in bank if x['chapter_id']==str(a.chapter)]
 scored=[]
 for x in rows:
  text=norm(x['content']); score=1.0 if text==target else (len(set(target.split())&set(text.split()))/max(1,len(set(target.split())|set(text.split()))))
  scored.append((score,x))
 scored.sort(key=lambda z:z[0],reverse=True)
 if not scored or scored[0][0]<0.72:
  print('NO_CONFIDENT_MATCH');return 2
 score,x=scored[0]
 print('MATCH_SCORE:',round(score,3));print('QUESTION_ID:',x['question_id']);print('TYPE:',x['type']);print('QUESTION:',x['content'])
 print('HINTS:')
 for h in x['hints']: print('-',h)
 print('CHOICES:')
 for c in x['choices']: print(c['id'],c['content'])
 print('NOTE: قاعدة البيانات لا تحتوي بالضرورة على مفتاح الإجابة؛ لم يتم اختيار إجابة أو إرسالها.')
 return 0
if __name__=='__main__': raise SystemExit(main())
