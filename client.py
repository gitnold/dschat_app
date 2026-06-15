import socket
import threading


def receive(conn):
    buffer = ""
    while True:
        try:
            data = conn.recv(1024)
            if not data:
                break
            buffer += data.decode()
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line and not line.startswith("__system__"):
                    print(line)
        except Exception:
            break


def run_client(host="127.0.0.1", port=65432):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, port))
        print(f"Connected to {host}:{port}")

        threading.Thread(target=receive, args=(s,), daemon=True).start()

        while True:
            try:
                msg = input()
                if msg.lower() in ("/quit", "/exit"):
                    break
                s.sendall((msg + "\n").encode())
            except (EOFError, KeyboardInterrupt):
                break


if __name__ == "__main__":
    run_client()
