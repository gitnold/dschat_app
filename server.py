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
    broadcast_clients_count()


def handle_client(conn):
    try:
        data = conn.recv(1024)
        if data:
            for line in data.decode().splitlines():
                line = line.strip()
                if not line:
                    continue
                client_id = clients[conn.fileno()]["id"]
                broadcast(f"User {client_id}: {line}")
        else:
            remove_client(conn)
    except Exception:
        remove_client(conn)


def broadcast(msg, exclude=None):
    disconnected = []
    for fd, info in list(clients.items()):
        if exclude is not None and info["conn"] == exclude:
            continue
        try:
            info["conn"].sendall((msg + "\n").encode())
        except Exception:
            disconnected.append(fd)
    for fd in disconnected:
        if fd in clients:
            remove_client(clients[fd]["conn"])


def broadcast_clients_count():
    count = len(clients)
    for info in clients.values():
        try:
            info["conn"].sendall(f"__system__ CLIENTS:{count}\n".encode())
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
        broadcast_clients_count()


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
