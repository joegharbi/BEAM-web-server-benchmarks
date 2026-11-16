#!/usr/bin/env python3
"""Do the variants answer exactly as the framework's server? Starts each image in Docker and compares the raw
answers to five requests (keep-alive, two on one connection, pipelined, Connection: close, HTTP/1.0) with those of
st-erlang-pure-29-1-1.

    ./srv/bin/python3 experiments/erlang-variants/check.py
"""
import socket
import subprocess
import sys
import time

PORT, NAME = 8001, "lt-check"
IMAGES = ["st-erlang-pure-29-1-1"] + [f"lt-erlang-{v}{bw}" for v in "abcde" for bw in ("", "-nobw")]
GET = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
CASES = {"keep-alive": [GET], "two requests": [GET, GET], "pipelined": [GET + GET],
         "split in two packets": [GET[:10], GET[10:]],
         "close": [b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"],
         "HTTP/1.0": [b"GET / HTTP/1.0\r\n\r\n"]}


def talk(parts):
    s = socket.create_connection(("localhost", PORT), timeout=3)
    out = b""
    for part in parts:
        s.sendall(part)
        time.sleep(0.15)
        s.settimeout(0.3)
        try:
            while True:
                data = s.recv(65536)
                if not data:
                    out += b"<closed>"
                    break
                out += data
        except socket.timeout:
            pass
    s.close()
    return out


def answers(image):
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", NAME, "-e", f"PORT={PORT}", "-p", f"{PORT}:{PORT}", image],
                   check=True, capture_output=True)
    try:
        for _ in range(40):
            try:
                socket.create_connection(("localhost", PORT), timeout=1).close()
                break
            except OSError:
                time.sleep(0.25)
        time.sleep(0.5)
        log = subprocess.run(["docker", "logs", NAME], capture_output=True, text=True).stdout.strip().splitlines()[-1]
        return {name: talk(parts) for name, parts in CASES.items()}, log
    finally:
        subprocess.run(["docker", "stop", "-t", "1", NAME], capture_output=True)


reference, _ = answers(IMAGES[0])
print(IMAGES[0], {k: (v.count(b"200 OK"), v.endswith(b"<closed>")) for k, v in reference.items()})
bad = 0
for image in IMAGES[1:]:
    got, log = answers(image)
    wrong = [k for k in CASES if got[k] != reference[k]]
    bad += len(wrong)
    print(f"{image:20} {log:16} {'same answers' if not wrong else 'DIFFERENT: ' + ', '.join(wrong)}")
    for k in wrong:
        print("   expected", reference[k][:200], "\n   got     ", got[k][:200])
sys.exit(1 if bad else 0)
