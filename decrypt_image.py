#!/usr/bin/env python3
"""解密微信 4.x V2 加密图片 (.dat) → 原图。

V2 格式 (.dat 头 07 08 56 32 08 07)：
  头 15 字节 + AES-128-ECB(前 N 字节, N=头里 offset6 的 LE u32) + 剩余字节 XOR xorKey
AES 钥 = 16 字符当 16 字节；xorKey = 一个字节。

用法: python3 decrypt_image.py <xxx.dat> [输出.jpg]
钥读自 ~/workspace/tools/WeFlow/.imagekey （第一行=aes钥, 第二行 xor=数字）
"""
import struct
import subprocess
import sys
from pathlib import Path

KEYFILE = Path.home() / "workspace" / "tools" / "WeFlow" / ".imagekey"
V2_MAGIC = bytes([0x07, 0x08, 0x56, 0x32, 0x08, 0x07])


def load_keys():
    aes = xor = None
    for line in KEYFILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("xor="):
            xor = int(line.split("=", 1)[1])
        elif line and "=" not in line:
            aes = line
    return aes.encode("ascii"), xor


def decrypt(dat: bytes, aes_key: bytes, xor: int) -> bytes:
    if dat[:6] != V2_MAGIC:
        return dat  # 非 V2，原样返回
    aeslen = struct.unpack("<I", dat[6:10])[0]
    hdr = 15
    aes = subprocess.run(
        ["openssl", "enc", "-d", "-aes-128-ecb", "-nopad", "-K", aes_key.hex()],
        input=dat[hdr:hdr + aeslen], capture_output=True).stdout
    rest = bytes(b ^ xor for b in dat[hdr + aeslen:])
    return aes + rest


if __name__ == "__main__":
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/decrypted.jpg"
    aes_key, xor = load_keys()
    res = decrypt(Path(src).read_bytes(), aes_key, xor)
    Path(out).write_bytes(res)
    print(f"{src} -> {out} ({len(res)} bytes, JPEG={res[:3] == b'\xff\xd8\xff'})")
