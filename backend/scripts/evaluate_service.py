"""本地客服固定题集评估；只向显式指定的回环服务发送题目，口令由环境提供。"""

import argparse
import http.cookiejar
import json
import os
import statistics
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def frames(text):
    for frame in text.replace('\r\n','\n').split('\n\n'):
        lines = frame.splitlines()
        kind = next((line[6:].strip() for line in lines if line.startswith('event:')), None)
        data = '\n'.join(line[5:].strip() for line in lines if line.startswith('data:'))
        if kind and data:
            yield kind,json.loads(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--username',required=True)
    parser.add_argument('--cases',type=Path,default=Path(__file__).parents[1]/'data/customer_service_eval.jsonl')
    args = parser.parse_args()
    if urlsplit(args.base_url).hostname not in ('127.0.0.1','localhost','::1'):
        parser.error('仅允许隔离本地服务；不要用真实客户账号运行题集')
    password = os.environ.get('EVAL_PASSWORD')
    if not password:
        parser.error('请通过 EVAL_PASSWORD 环境变量提供验证账号口令')
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def post(path,body):
        request = urllib.request.Request(args.base_url.rstrip('/')+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=210) as response:
            return response.read().decode()
    post('/api/auth/login',{'username':args.username,'password':password})
    rows=[]
    for line in args.cases.read_text().splitlines():
        if not line.strip():
            continue
        case=json.loads(line)
        start=time.perf_counter()
        events=list(frames(post('/api/chat/stream',{'message':case['message'],'client_message_id':str(uuid4())})))
        result=next((value for kind,value in reversed(events) if kind=='final'),{})
        intent=next((value.get('intent') for kind,value in reversed(events) if kind=='intent'),None)
        actual=[result.get('intent') or intent]
        expected=case.get('intents')
        intent_ok=not expected or (bool(set(actual)&set(expected)) if case.get('intent_mode')=='any' else sorted(actual)==sorted(expected))
        text=result.get('text','')
        correct=bool(text) and intent_ok and result.get('confirmationRequired',False)==case['confirmation']
        correct=correct and (not case.get('contains_any') or any(word in text for word in case['contains_any']))
        correct=correct and not any(word in text for word in case.get('forbidden',[]))
        row={'id':case['id'],'pass':correct,'intent_ok':intent_ok,'intents':actual,'elapsed_ms':round((time.perf_counter()-start)*1000),'usage':result.get('usage',{}),'text':text}
        rows.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    latencies=sorted(row['elapsed_ms'] for row in rows)
    print(json.dumps({'cases':len(rows),'passed':sum(row['pass'] for row in rows),'intent_accuracy':sum(row['intent_ok'] for row in rows)/len(rows),
                      'p50_ms':statistics.median(latencies),'p95_ms':latencies[min(len(latencies)-1,int(len(latencies)*.95))],
                      'estimated_usd':sum(row['usage'].get('estimated_usd') or 0 for row in rows) if all(row['usage'].get('estimated_usd') is not None for row in rows) else None},ensure_ascii=False))
    if not all(row['pass'] for row in rows):
        raise SystemExit(1)


if __name__=='__main__':
    main()
