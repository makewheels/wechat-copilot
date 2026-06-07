import os, sys, socket, base64, select
host, port = sys.argv[1], sys.argv[2]
user = os.environ["PXUSER"]; pw = os.environ["PXPASS"]
auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
s = socket.create_connection(("127.0.0.1", 28080), timeout=15)
s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode())
buf = b""
while b"\r\n\r\n" not in buf:
    d = s.recv(1)
    if not d:
        sys.stderr.write("proxy closed during CONNECT\n"); sys.exit(1)
    buf += d
status = buf.split(b"\r\n", 1)[0]
if b" 200 " not in status:
    sys.stderr.write("CONNECT failed: " + status.decode("latin1", "replace") + "\n"); sys.exit(1)
while True:
    r, _, _ = select.select([0, s], [], [])
    if 0 in r:
        data = os.read(0, 65536)
        if not data: break
        s.sendall(data)
    if s in r:
        data = s.recv(65536)
        if not data: break
        os.write(1, data)
