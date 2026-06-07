#!/usr/bin/env python3
"""WinRM helper. Reads WINHOST/WINPORT/WINUSER/WINPASS from env.

  winrm.py ps            < script.ps1      # run PowerShell from stdin
  winrm.py cmd           < cmdline         # run cmd from stdin
  winrm.py putfile L R                     # upload local L -> remote R
  winrm.py getfile R                       # print remote file R to stdout
"""
import os, sys
from pathlib import Path

from pypsrp.client import Client


def _load_env():
    f = Path(__file__).resolve().parent / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_env()

c = Client(os.environ.get("WINHOST", "127.0.0.1"),
           port=int(os.environ.get("WINPORT", "15985")),
           username=os.environ["WINUSER"], password=os.environ["WINPASS"],
           ssl=False, auth="ntlm", cert_validation=False, connection_timeout=40)

mode = sys.argv[1]
if mode == "putfile":
    c.copy(sys.argv[2], sys.argv[3])
    print(f"uploaded -> {sys.argv[3]}")
elif mode == "getfile":
    out, streams, err = c.execute_ps(
        f"[IO.File]::ReadAllText('{sys.argv[2]}',[Text.Encoding]::UTF8)")
    sys.stdout.write(out or "")
elif mode == "cmd":
    out, err, rc = c.execute_cmd(sys.stdin.read())
    sys.stdout.write(out or "")
    if err: sys.stderr.write("\n[stderr]\n" + err)
    print(f"\n[rc={rc}]")
else:  # ps
    out, streams, err = c.execute_ps(sys.stdin.read())
    sys.stdout.write(out or "")
    if err:
        for e in streams.error: sys.stderr.write("\n[ps-error] " + str(e))
    sys.stderr.write(f"\n[had_error={err}]\n")
