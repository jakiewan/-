"""从项目根目录运行 python -m backend.rag --help 查看直接检索用法。"""

import argparse
import json

from .retriever import CATEGORIES, CHANNELS, FaqRetriever


def main():
    parser = argparse.ArgumentParser(
        description="只读查询现有 FAQ；答案可能为模拟政策，查看 simulated 标记",
        epilog="连接配置可在项目 .env 中设置 RAG_MILVUS_URI、RAG_MILVUS_DB、RAG_REDIS_URL；默认使用本地已有服务。",
    )
    parser.add_argument("query", help="要检索的问题")
    parser.add_argument("--goodsid", required=True, choices=CATEGORIES, help="65539 手机 / 65540 电脑 / 65541 空调 / 65542 冰箱 / 65543 洗衣机")
    parser.add_argument("--channel", default="MALL", choices=CHANNELS, help="默认 MALL 商城，无需传入；DIRECT 自营店 / AUTHORIZED 授权店")
    args = parser.parse_args()
    retriever = None
    try:
        retriever = FaqRetriever()
        result = retriever.search(args.query, goodsid=args.goodsid, sales_channel=args.channel)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # 避免第三方连接异常将含凭据的地址直接写入终端。
        parser.exit(1, f"FAQ 检索失败（{type(exc).__name__}），请检查模型缓存、RAG 连接配置及知识数据。\n")
    finally:
        if retriever is not None:
            retriever.close()


if __name__ == "__main__":
    main()
