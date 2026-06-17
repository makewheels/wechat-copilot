#!/usr/bin/env python3
"""建/重建全局聊天记录向量库（所有人+群）。跑一次即可，之后偶尔重跑更新。

用法: uv run --with numpy python index_all.py
"""
import core
import rag


def main():
    env = core.load_env()
    n = rag.build(env)
    print(f"完成，共 {n} 块。现在可在问答网页用「全局搜索」了。")


if __name__ == "__main__":
    main()
