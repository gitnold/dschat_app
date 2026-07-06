import socket
import selectors

sel = selectors.DefaultSelector()
clients = {}
next_id = 1


def accept(server_sock):
    global next_id
    conn, addr = server_sock.accept()
    conn.setblocking(False)
    client_id = next_id
    next_id += 1
    clients[conn.fileno()] = {"id": client_id, "conn": conn, "addr": str(addr)}
    sel.register(conn, selectors.EVENT_READ, handle_client)

    conn.sendall(f"__system__ Your ID: {client_id}\n".encode())
    broadcast(f"System: User {client_id} joined (total: {len(clients)})")
    broadcast_user_list()


def handle_client(conn):
    try:
        data = conn.recv(1024)
        if data:
            for line in data.decode().splitlines():
                line = line.strip()
                if not line:
                    continue
                client_info = clients.get(conn.fileno())
                if not client_info:
                    continue
                client_id = client_info["id"]

                parts = line.split(maxsplit=2)
                if len(parts) >= 3 and parts[0] == "/msg":
                    try:
                        target_id = int(parts[1])
                        message = parts[2]
                    except ValueError:
                        continue
                    send_private(client_id, target_id, message)
                elif parts[0] == "__key__":
                    relay_key(client_id, parts[1] if len(parts) > 1 else "")
                else:
                    broadcast(f"User {client_id}: {line}")
        else:
            remove_client(conn)
    except Exception:
        remove_client(conn)


def send_private(from_id, to_id, message):
    sender_info = None
    recipient_info = None
    for info in clients.values():
        if info["id"] == from_id:
            sender_info = info
        if info["id"] == to_id:
            recipient_info = info

    if not recipient_info:
        if sender_info:
            try:
                sender_info["conn"].sendall(
                    f"__system__ User {to_id} is not online\n".encode()
                )
            except Exception:
                pass
        return

    msg = f"__private__ from {from_id} to {to_id}: {message}"

    try:
        recipient_info["conn"].sendall((msg + "\n").encode())
    except Exception:
        remove_client(recipient_info["conn"])

    try:
        sender_info["conn"].sendall((msg + "\n").encode())
    except Exception:
        remove_client(sender_info["conn"])


def relay_key(from_id, key_data):
    for info in clients.values():
        if info["id"] == from_id:
            info["key"] = key_data
            break

    msg = f"__key__ from {from_id}: {key_data}"
    for info in clients.values():
        if info["id"] != from_id:
            try:
                info["conn"].sendall((msg + "\n").encode())
            except Exception:
                pass

    for info in clients.values():
        if info["id"] == from_id:
            sender_conn = info["conn"]
            for existing in clients.values():
                if existing["id"] != from_id and existing.get("key"):
                    try:
                        sender_conn.sendall(
                            (f"__key__ from {existing['id']}: {existing['key']}\n").encode()
                        )
                    except Exception:
                        pass
            break


def broadcast(msg):
    disconnected = []
    for fd, info in list(clients.items()):
        try:
            info["conn"].sendall((msg + "\n").encode())
        except Exception:
            disconnected.append(fd)
    for fd in disconnected:
        if fd in clients:
            remove_client(clients[fd]["conn"])


def broadcast_user_list():
    user_ids = [str(info["id"]) for info in clients.values()]
    msg = f"__system__ USERLIST:{','.join(user_ids)}"
    for info in clients.values():
        try:
            info["conn"].sendall((msg + "\n").encode())
        except Exception:
            pass


def remove_client(conn):
    if conn.fileno() in clients:
        info = clients.pop(conn.fileno())
        try:
            sel.unregister(conn)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        broadcast(f"System: User {info['id']} left (total: {len(clients)})")
        broadcast_user_list()


def run_server(host="127.0.0.1", port=65432):
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen()
    server_sock.setblocking(False)
    sel.register(server_sock, selectors.EVENT_READ, accept)
    print(f"Server listening on {host}:{port}", flush=True)
    try:
        while True:
            events = sel.select(timeout=None)
            for key, _ in events:
                callback = key.data
                callback(key.fileobj)
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        for info in list(clients.values()):
            try:
                info["conn"].close()
            except Exception:
                pass
        sel.close()
        server_sock.close()


if __name__ == "__main__":
    run_server()
