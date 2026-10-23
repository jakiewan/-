"""导出无正文删除日志；恢复旧备份后、开放服务前重放，防止已删除记忆复活。"""

import argparse
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

from ..common.constants import PROJECT_ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['export','apply'])
    parser.add_argument('path',type=Path)
    args=parser.parse_args()
    load_dotenv(PROJECT_ROOT/'.env')
    database=os.environ['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
    with psycopg.connect(database,row_factory=dict_row) as conn:
        if args.operation=='export':
            rows=conn.execute('''SELECT e.tenant_id,e.user_id,e.epoch,e.memory_key,e.scope_id,
                s.memory_epoch,s.history_cutoff,s.capture_after,s.blocked_keys,s.auto_capture
                FROM customer_chat.memory_events e JOIN customer_chat.memory_settings s USING (tenant_id,user_id)
                WHERE e.action LIKE 'forget:%%' ORDER BY e.id''').fetchall()
            # 不覆盖已有删除日志，避免误把较旧导出覆盖最新恢复依据。
            with args.path.open('x') as out:
                os.chmod(args.path,0o600)
                json.dump(rows,out,ensure_ascii=False)
            print(f'已导出 {len(rows)} 条无正文删除记录')
            return
        rows=json.loads(args.path.read_text())
        for row in rows:
            tenant,user=row['tenant_id'],row['user_id']
            conn.execute('SELECT id FROM customer_chat.conversations WHERE user_id=%s FOR UPDATE',(user,))
            conn.execute('''UPDATE customer_chat.memory_items SET status='deleted',value='',quote=''
                WHERE tenant_id=%s AND user_id=%s AND epoch<%s
                AND (%s::text IS NULL OR (memory_key=%s AND scope_id=%s))''',
                (tenant,user,row['epoch'],row['memory_key'],row['memory_key'],row['scope_id']))
            conn.execute('''INSERT INTO customer_chat.memory_settings
                (tenant_id,user_id,memory_epoch,history_cutoff,capture_after,blocked_keys,auto_capture,migrated)
                VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE) ON CONFLICT (tenant_id,user_id) DO UPDATE SET
                memory_epoch=GREATEST(customer_chat.memory_settings.memory_epoch,EXCLUDED.memory_epoch),
                history_cutoff=GREATEST(customer_chat.memory_settings.history_cutoff,EXCLUDED.history_cutoff),
                capture_after=GREATEST(customer_chat.memory_settings.capture_after,EXCLUDED.capture_after),
                blocked_keys=CASE WHEN customer_chat.memory_settings.memory_epoch<=EXCLUDED.memory_epoch
                    THEN EXCLUDED.blocked_keys ELSE customer_chat.memory_settings.blocked_keys END,
                auto_capture=FALSE,migrated=TRUE''',
                (tenant,user,row['memory_epoch'],row['history_cutoff'],row['capture_after'],Jsonb(row['blocked_keys']),False))
            conn.execute("UPDATE customer_chat.memory_jobs SET status='cancelled',candidates=NULL WHERE tenant_id=%s AND user_id=%s AND epoch<%s",(tenant,user,row['memory_epoch']))
            conn.execute('UPDATE customer_chat.conversations SET version=version+1 WHERE user_id=%s',(user,))
        # 旧备份的序列不能生成小于删除时历史隔离点的新消息编号。
        conn.execute("""SELECT setval(pg_get_serial_sequence('customer_chat.conversation_messages','id'),
            GREATEST((SELECT coalesce(max(id),1) FROM customer_chat.conversation_messages),
                     (SELECT coalesce(max(history_cutoff),1) FROM customer_chat.memory_settings)),TRUE)""")
        print(f'已重放 {len(rows)} 条删除记录；自动记忆保持关闭，请核对后再开放服务')


if __name__=='__main__':
    main()
