# dschat_app — Technical Architecture & Implementation Guide

A multi-client TCP chat application in Python with a broadcast/private-messaging server, a minimal CLI client, and a rich TUI client built with Textual.

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Wire Protocol](#2-wire-protocol)
3. [Server (`server.py`)](#3-server-serverpy)
4. [CLI Client (`client.py`)](#4-cli-client-clientpy)
5. [TUI Client (`client_tui.py`)](#5-tui-client-client_tuipy)
6. [Data Flow](#6-data-flow)

---

## 1. Project Structure

```
dschat_app/
  server.py          Single-threaded event-driven TCP server
  client.py          Minimal CLI client (stdin/stdout + thread)
  client_tui.py      Full TUI client using the Textual framework
  requirements.txt   Dependencies (textual>=0.41.0)
```

- **Server** — uses only the Python standard library (`socket`, `selectors`).
- **CLI client** — uses only the standard library (`socket`, `threading`).
- **TUI client** — uses the third-party [Textual](https://textual.textualize.io/) framework for a rich terminal UI, plus `asyncio` and `re` from the standard library.

---

## 2. Wire Protocol

All communication happens over **TCP** (IPv4) on `127.0.0.1:65432` by default. Every message is a **newline-terminated string**. There is no framing, length-prefixing, or serialisation format — the newline character (`\n`) acts as the message delimiter.

The server distinguishes three categories of outbound message by examining the prefix:

| Prefix | Server→Client | Purpose |
|---|---|---|
| `__system__` | `__system__ Your ID: 3\n` | Assigns a numeric ID on connect |
| `__system__` | `__system__ USERLIST:1,2,3\n` | Informs all clients of the current online user set |
| `__system__` | `__system__ User 5 is not online\n` | Error reply to a `/msg` targeting a disconnected user |
| `__private__` | `__private__ from 1 to 3: hello\n` | Delivers a private message to both sender and recipient |
| *(none)* | `User 1: hello everyone\n` | Public broadcast message |

Client-to-server commands:

| Client→Server | Behaviour |
|---|---|
| `<any text>\n` | Treated as a broadcast message; the server prepends `User <id>: ` and sends to all |
| `/msg <target_id> <text>\n` | Private message; the server validates the target exists and relays the message |
| *(TCP connection closed)* | Server detects EOF during `recv` and removes the client |

---

## 3. Server (`server.py`)

### 3.1 Imports and Global State

```python
import socket
import selectors

sel = selectors.DefaultSelector()
clients = {}
next_id = 1
```

- **`socket`** — provides the TCP socket API (create, bind, listen, accept, send, recv).
- **`selectors`** — a high-level I/O multiplexing abstraction over `select`/`epoll`/`kqueue`. `DefaultSelector` picks the most efficient implementation available on the current platform (epoll on Linux, kqueue on macOS, select elsewhere). This lets the server handle many connections in a single thread without blocking on any one of them.
- **`sel`** — the global selector instance that monitors all sockets (listening + client).
- **`clients`** — a dictionary keyed by file descriptor (`conn.fileno()`) mapping to `{"id", "conn", "addr"}`. This is the server's entire notion of "state per connection".
- **`next_id`** — a monotonically increasing integer counter used to assign unique, human-friendly IDs to each connecting client. Using `next_id` means IDs are never reused within a single server session.

### 3.2 `accept(server_sock)` — Handling New Connections

```python
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
```

- `server_sock.accept()` — blocks until a client connects, then returns a new socket `conn` for that client and the remote address `addr`.
- `conn.setblocking(False)` — puts the client socket into non-blocking mode. Without this, `recv()` and `sendall()` could block the entire event loop. In non-blocking mode they raise `BlockingIOError` if the operation would block, but the selector guarantees we only read/write when the socket is ready, so we never hit that error in practice.
- **ID assignment** — `next_id` is captured before incrementing. The global is modified here and in no other function.
- **Registration** — `sel.register(conn, selectors.EVENT_READ, handle_client)` tells the selector: "watch this socket for read-readiness, and when it's ready, call `handle_client`". The third positional argument (`key.data`) stores the callback function, which is retrieved later as `key.data` in the event loop.
- **ID notification** — the client is told its own ID immediately via a `__system__` message so the TUI client can colour its own messages differently and filter private message routing.
- **Broadcasts** — `broadcast(...)` sends a human-readable join notification. `broadcast_user_list()` sends the machine-readable user list that the TUI client uses to build the sidebar.

### 3.3 `handle_client(conn)` — Processing Inbound Data

```python
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
                else:
                    broadcast(f"User {client_id}: {line}")
        else:
            remove_client(conn)
    except Exception:
        remove_client(conn)
```

- `conn.recv(1024)` — reads up to 1024 bytes from the socket. The buffer size is an arbitrary choice that works well for chat messages. Because TCP is a stream protocol, one `send()` on the client may arrive as multiple `recv()` calls and vice versa, so the server must handle message boundaries.
- **Message boundary handling** — `data.decode().splitlines()` splits on any line-break sequence (`\n`, `\r\n`). This correctly handles the case where multiple messages arrive in a single `recv()` call. However, it does **not** handle partial messages where a message is split across two `recv()` calls — this is a known limitation.
- `line.strip()` — removes trailing `\r` or whitespace that could remain from the split.
- **Lookup** — `clients.get(conn.fileno())` retrieves the client info by file descriptor. This is safe even if the client was already removed (returns `None`, and we `continue`).
- **Command parsing** — `parts = line.split(maxsplit=2)` splits into at most 3 parts: the command verb, the argument, and the rest (the message body). We check for at least 3 parts and the verb `/msg`. `target_id` is cast to `int` — if it fails (`ValueError`), the malformed command is silently ignored.
- **Routing** — if the line is a `/msg` command, we call `send_private(...)`. Otherwise it is treated as a broadcast.

### 3.4 `send_private(from_id, to_id, message)` — Private Message Delivery

```python
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
```

- **Lookup by ID** — iterates over all client entries to find sender and recipient by their numeric IDs. This is O(n) but acceptable for a chat application (typically < 1,000 users).
- **Ghost detection** — if `recipient_info` is `None`, the target user is not online. The sender is notified via a `__system__` error message. If the sender themselves disconnected between parsing the command and executing it, `sender_info` might be `None`, in which case we silently return.
- **Delivery** — the same formatted string `__private__ from X to Y: message` is sent to **both** the sender and the recipient. The sender's copy lets their UI confirm the message was sent (echo). The client UI distinguishes direction by comparing `from_id` to its own `my_id`.

### 3.5 `broadcast(msg)` — Sending to All Clients

```python
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
```

- **Iterate over a copy** — `list(clients.items())` creates a snapshot of the dictionary so that modifying `clients` during iteration (in `remove_client`) does not raise `RuntimeError: dictionary changed size during iteration`.
- **Graceful failure** — if `sendall()` raises an exception (e.g. the client disconnected ungracefully), we collect the file descriptor and clean up after the loop rather than during iteration.

### 3.6 `broadcast_user_list()` — Syncing Online Users

```python
def broadcast_user_list():
    user_ids = [str(info["id"]) for info in clients.values()]
    msg = f"__system__ USERLIST:{','.join(user_ids)}"
    for info in clients.values():
        try:
            info["conn"].sendall((msg + "\n").encode())
        except Exception:
            pass
```

- The `USERLIST` message contains a comma-separated list of numeric IDs. It is sent to every client whenever the user list changes (join or leave). The TUI client uses this to dynamically build and rebuild its sidebar of online users. The CLI client ignores it (it filters out `__system__` messages).

### 3.7 `remove_client(conn)` — Clean Disconnection

```python
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
```

- **Guard** — `if conn.fileno() in clients` prevents double-removal if `remove_client` is called multiple times for the same connection.
- **Order** — pop from tracking dict first, then unregister from the selector, then close the socket. This ensures the connection is no longer tracked even if the subsequent steps fail.
- **Notifications** — broadcasts the departure and refreshes the user list, exactly mirroring the join flow.

### 3.8 `run_server(host, port)` — Bootstrap & Event Loop

```python
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
```

- **`AF_INET` / `SOCK_STREAM`** — creates an IPv4 TCP socket.
- **`SO_REUSEADDR`** — allows the server to bind to the same address immediately after a restart, avoiding `Address already in use` errors during the `TIME_WAIT` period.
- **`server_sock.setblocking(False)`** — the listening socket must be non-blocking so that `accept()` in the event loop does not block waiting for new connections.
- **`sel.register(server_sock, ..., accept)`** — registers the listening socket for read events with `accept` as the callback. When `accept` returns, the new client socket is registered separately.
- **The event loop** — `sel.select(timeout=None)` blocks indefinitely until at least one registered socket is ready. It returns a list of `(key, events)` tuples. `key.data` is the callback we stored during registration, and `key.fileobj` is the socket. We call `callback(key.fileobj)` — so for the listening socket this calls `accept(server_sock)`, and for client sockets it calls `handle_client(conn)`.
- **`KeyboardInterrupt`** — catches Ctrl+C, prints a message, and falls through to `finally`.
- **Cleanup** — closes all client connections, the selector (which closes all its registered file descriptors), and the server socket.

---

## 4. CLI Client (`client.py`)

The CLI client is intentionally minimal: it provides a raw stdin/stdout interface with no UI logic.

```python
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
```

- **`buffer`** — a string accumulator that reassembles TCP stream chunks into complete newline-delimited messages. This addresses the "partial message" problem on the receiving end (the server does not handle it on its end because it assumes `recv` returns complete lines — which is not guaranteed by TCP).
- **`data = conn.recv(1024)`** — blocking read. Because `recv` is called from a dedicated thread, blocking the receiver thread does not affect the main input thread.
- **`if not data: break`** — an empty byte string signals that the remote end closed the connection (TCP FIN).
- **`while "\n" in buffer`** — processes all complete lines from the buffer before returning to `recv`. This prevents unbounded buffer growth if the sender spams data.
- **`line.startswith("__system__")`** — system messages are silently discarded because this client has no UI concept of IDs, user lists, or private message routing. Only broadcast messages (`User X: text`) and private messages (`__private__ from ...`) pass through.

```python
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
```

- **`with socket.socket(...) as s:`** — the socket is automatically closed when the `with` block exits (the main loop breaks).
- **`threading.Thread(target=receive, args=(s,), daemon=True)`** — spawns a daemon thread that runs `receive(s)`. Daemon threads are killed automatically when the main thread exits, so there is no need to explicitly join them.
- **`input()`** — blocks the main thread waiting for user input. Every line typed is sent verbatim over the socket. If the line is `/quit` or `/exit`, the loop breaks.
- **`s.sendall((msg + "\n").encode())`** — appends the newline delimiter and encodes the string to bytes before sending. `sendall` retries internally until all bytes are sent, unlike `send` which may send a partial buffer.

**Limitation**: the CLI client does not parse or display private messages differently from broadcasts — both appear as raw text. The `__private__` prefix is not stripped because it does not start with `__system__`.

---

## 5. TUI Client (`client_tui.py`)

The TUI client is a full-featured chat interface built with the [Textual](https://textual.textualize.io/) framework. Its architecture is asynchronous throughout, using Python's `asyncio` event loop (which Textual runs internally).

### 5.1 Class Definition and CSS

```python
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Input, RichLog, Static, Button
from textual.binding import Binding


class ChatTUI(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #status_row {
        height: 1;
        background: $panel;
        padding: 0 1;
        layout: horizontal;
    }
    #main_container {
        layout: horizontal;
        height: 1fr;
    }
    #sidebar {
        width: 24;
        border: solid $primary;
        margin: 0 0 0 1;
        background: $panel;
        height: 1fr;
        overflow-y: auto;
    }
    #chat_log {
        border: solid $primary;
        height: 1fr;
        margin: 0 1;
    }
    #input_row {
        height: 3;
        margin: 0 1 1 1;
        layout: horizontal;
    }
    #msg_input {
        width: 1fr;
    }
    #send_btn {
        width: 10;
        margin-left: 1;
    }
    .sidebar-btn {
        width: 100%;
        height: 3;
        min-height: 3;
    }
    """

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit"),
    ]
```

- **`App`** — Textual's base class for terminal applications.
- **`CSS`** — a class variable containing Textual CSS (a subset of web CSS). `$surface`, `$panel`, `$primary`, `$text-disabled` are theme variables resolved by Textual's built-in theme system. `1fr` means "one fraction of remaining space", which is how Textual implements flexible layouts. The sidebar is fixed at 24 columns, while the chat area fills the rest.
- **`BINDINGS`** — registers `Ctrl+D` as a global keybinding to `action_quit`.

### 5.2 Constructor — State Initialisation

```python
def __init__(self, host="127.0.0.1", port=65432):
    super().__init__()
    self.host = host
    self.port = port
    self.reader = None
    self.writer = None
    self._connected = False
    self.my_id = None
    self._connecting = False
    self.messages = []
    self.current_chat = "all"
    self.online_users = set()
```

- **`reader` / `writer`** — `asyncio` stream objects obtained from `asyncio.open_connection`.
- **`my_id`** — set when the server sends `__system__ Your ID: N`. Used to colour own messages green and to determine which private messages involve this client.
- **`messages`** — an in-memory list of all received messages (both broadcast and private). Each entry is a dict with keys `type` ("broadcast"|"private"), `from_id`, `to_id`, and `formatted` (a string with Textual markup). This list enables chat-switching without re-fetching from the server.
- **`current_chat`** — tracks which chat view is active: `"all"` for the public channel, or a user ID string for private chat.
- **`online_users`** — a set of ID strings parsed from `USERLIST` messages. Drives the dynamic sidebar buttons.

### 5.3 `compose()` — Building the UI Tree

```python
def compose(self):
    yield Header(show_clock=True)
    yield Horizontal(
        Static("", id="server_status"),
        Static("", id="client_count"),
        id="status_row",
    )
    with Horizontal(id="main_container"):
        with Vertical(id="sidebar"):
            yield Button("All", id="user_all", variant="primary", classes="sidebar-btn")
            yield Static("── Online ──", id="sidebar_divider")
        with Vertical(id="chat_area"):
            yield Static("Chat: All", id="chat_header")
            yield RichLog(id="chat_log", highlight=True, markup=True, wrap=True, max_lines=1000)
            yield Horizontal(
                Input(placeholder="Message everyone...", id="msg_input"),
                Button("Send", id="send_btn", variant="primary"),
                id="input_row",
            )
    yield Footer()
```

- `compose` is a generator that yields Textual widgets. Textual mounts them in order to construct the widget tree. The layout is:

```
  Header
  Status Row: [Server Status] [Client Count]
  ┌──────────┬──────────────────────────────────┐
  │ Sidebar  │ Chat Header                      │
  │ [All]    │ Chat Log (RichLog)               │
  │ ──Online─│                                   │
  │ ● User 2 │                                   │
  │ ● User 3 │                                   │
  │          │                                   │
  │          ├──────────────────────────────────┤
  │          │ [Input              ] [Send]      │
  └──────────┴──────────────────────────────────┘
  Footer
```

- **`RichLog`** — a Textual widget optimised for streaming log output. `highlight=True` enables syntax highlighting, `markup=True` enables Textual markup tags (e.g. `[bold green]...[/bold green]`), `max_lines=1000` limits the scrollback buffer.
- **`Button("All", variant="primary")`** — the "All" button in the sidebar is the default for switching to the public broadcast view. `variant="primary"` gives it a highlighted (accent-coloured) style.

### 5.4 `on_mount()` — Starting the Connection

```python
def on_mount(self):
    asyncio.create_task(self.connect())
```

- `on_mount` is a Textual lifecycle hook called after the UI is fully assembled. It launches the `connect` coroutine as an `asyncio` Task. This is the application's entry point into the networking layer.

### 5.5 `connect()` — Establishing the TCP Connection

```python
async def connect(self):
    if self._connecting:
        return
    self._connecting = True
    self.log_message(f"[yellow]Connecting to {self.host}:{self.port}...[/yellow]")
    try:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self._connected = True
        self._connecting = False
        self.set_connection_status(True)
        self.log_message("[bold green]Connected to server[/bold green]")
        self.query_one("#msg_input", Input).disabled = False
        self.query_one("#msg_input", Input).focus()
        asyncio.create_task(self.listen())

    except (ConnectionRefusedError, OSError) as e:
        self._connected = False
        self._connecting = False
        self.set_connection_status(False)
        self.log_message(f"[red]Connection failed: {e}[/red]")
        self.query_one("#msg_input", Input).disabled = True
        self.query_one("#client_count", Static).update("")
        await asyncio.sleep(5)
        asyncio.create_task(self.connect())
```

- **`_connecting` guard** — prevents multiple concurrent connection attempts (e.g. if the user triggers a reconnect while one is already in progress).
- **`asyncio.open_connection`** — an async high-level API that creates a TCP connection and returns `(reader, writer)` stream objects. `reader.readline()` is the primary method used in `listen()`.
- **`set_connection_status(True)`** — updates the status bar widget to show a green "Connected" indicator.
- **`query_one(...).disabled = False`** — enables the input widget so the user can type messages.
- **`query_one(...).focus()`** — moves keyboard focus to the input box so the user can start typing immediately without clicking.
- **`asyncio.create_task(self.listen())`** — starts the listen loop as a background task.
- **Reconnection** — on failure, the input is disabled, and after a 5-second delay, a new `connect()` task is spawned. This pattern is recursive: each failure schedules the next attempt indefinitely.

### 5.6 `listen()` — The Receive Loop

```python
async def listen(self):
    try:
        while True:
            data = await self.reader.readline()
            if not data:
                break
            msg = data.decode().strip()
            if msg.startswith("__system__"):
                self.handle_system_message(msg)
            elif msg.startswith("__private__"):
                self.handle_private_message(msg)
            else:
                self.handle_broadcast(msg)
    
    except asyncio.CancelledError:
        pass

    except Exception as e:
        self.log_message(f"[red]Connection lost: {e}[/red]")

    finally:
        self._connected = False
        self.set_connection_status(False)
        self.query_one("#msg_input", Input).disabled = True
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        if not self._connecting:
            self.messages.clear()
            self.current_chat = "all"
            self.online_users.clear()
            self.update_sidebar()
            self.update_chat_header()
            asyncio.create_task(self.connect())
```

- **`reader.readline()`** — an async method that reads bytes until a newline character (`\n`) is encountered, then returns the complete line (including the newline). This is a built-in framing mechanism in `asyncio` streams that eliminates the manual buffering the CLI client needs.
- **`if not data: break`** — an empty bytes object means EOF (server closed the connection).
- **Message routing** — the first two characters of each message determine its type: `__system__`, `__private__`, or broadcast (anything else).
- **`finally` block** — runs regardless of how the loop exits (normal EOF, exception, cancellation). It resets connection state, clears the message history, and schedules a reconnection — unless `_connecting` is `True` (which means we are already in the middle of a connect attempt).

### 5.7 System Message Handling

```python
def handle_system_message(self, msg):
    content = msg[len("__system__"):].strip()
    if content.startswith("Your ID:"):
        self.my_id = content.split(":")[1].strip()
        self.log_message(f"[bold cyan]You are User {self.my_id}[/bold cyan]")
    elif content.startswith("USERLIST:"):
        ids_str = content.split(":", 1)[1].strip()
        self.online_users = set(ids_str.split(",")) if ids_str else set()
        self.update_sidebar()
    elif content.startswith("User ") and "is not online" in content:
        self.log_message(f"[red]{content}[/red]")
```

- Strips the `__system__` prefix to get the payload.
- `Your ID:` — extracts the numeric ID and stores it in `self.my_id`. This is used later to determine message ownership.
- `USERLIST:` — splits the comma-separated list into a set. The set is stored and used to rebuild the sidebar buttons via `update_sidebar()`. If the list is empty (no other users), the set is empty.
- `User X is not online` — displayed as a red error in the chat log.

### 5.8 Broadcast Message Handling

```python
def handle_broadcast(self, msg):
    match = re.match(r'^User\s+(\d+):\s*(.*)', msg)
    if match:
        user_id = match.group(1)
        text = match.group(2)
        if user_id == self.my_id:
            formatted = f"[bold green]You: {text}[/bold green]"
        else:
            formatted = f"User {user_id}: {text}"
    else:
        formatted = f"[dim]{msg}[/dim]"

    self.messages.append({
        "type": "broadcast",
        "raw": msg,
        "formatted": formatted,
    })

    if self.current_chat == "all":
        self.query_one("#chat_log", RichLog).write(formatted)
```

- The regex `r'^User\s+(\d+):\s*(.*)'` parses the server's broadcast format `User <id>: <message>`. Group 1 is the user ID, group 2 is the message body.
- If the user ID matches `self.my_id`, the message is displayed as `You:` in bold green — this is the echo confirmation of the user's own broadcast.
- If the regex does not match (e.g. a `System: ...` message arrives without the `__system__` prefix for some reason), it falls back to dimmed raw text.
- The formatted message (with Textual markup tags) is appended to `self.messages` and conditionally written to the chat log depending on the current chat mode.

### 5.9 Private Message Handling

```python
def handle_private_message(self, msg):
    rest = msg[len("__private__"):].strip()
    match = re.match(r'^from\s+(\d+)\s+to\s+(\d+):\s*(.*)', rest)
    if not match:
        return
    from_id = match.group(1)
    to_id = match.group(2)
    text = match.group(3)

    if from_id == self.my_id:
        formatted = f"[bold cyan]You -> User {to_id}: {text}[/bold cyan]"
        chat_partner = to_id
    elif to_id == self.my_id:
        formatted = f"[bold yellow]User {from_id} (private): {text}[/bold yellow]"
        chat_partner = from_id
    else:
        return

    entry = {
        "type": "private",
        "from_id": from_id,
        "to_id": to_id,
        "formatted": formatted,
    }
    self.messages.append(entry)

    if self.current_chat == chat_partner:
        self.query_one("#chat_log", RichLog).write(formatted)
```

- Strips the `__private__` prefix and regex-parses the rest to extract `from_id`, `to_id`, and the message body.
- **Direction detection**: if `from_id` equals `my_id`, this is an outgoing private message (echo from the server). If `to_id` equals `my_id`, it is an incoming private message. If neither matches (which should not happen in normal operation), the message is silently discarded.
- The `chat_partner` variable determines which private chat view should display this message. It is either the `to_id` (if we are the sender) or the `from_id` (if we are the recipient). This ensures that when chatting with User 2, only messages between you and User 2 appear.
- The message is stored in `self.messages` and conditionally displayed only if the current chat matches the partner.

### 5.10 UI Updates and Chat Management

```python
def switch_chat(self, target):
    if target == self.current_chat:
        return
    if target != "all" and target not in self.online_users:
        return
    self.current_chat = target
    self.update_chat_header()
    self.update_input_placeholder()
    self.rebuild_chat_log()
    self.update_sidebar_active()
```

- Switching to the same target is a no-op.
- Switching to a user who is not online is also a no-op (prevents clicking stale sidebar buttons).
- The switch updates: the header (e.g. "Chat: All" → "Chat: User 3 (private)"), the input placeholder, the chat log content (filtered to the selected view), and the sidebar button variants (highlight the active selection).

```python
def rebuild_chat_log(self):
    chat_log = self.query_one("#chat_log", RichLog)
    chat_log.clear()
    for entry in self.messages:
        if self.current_chat == "all":
            if entry["type"] == "broadcast":
                chat_log.write(entry["formatted"])
        else:
            if entry["type"] == "private":
                if (entry["from_id"] == self.current_chat and entry["to_id"] == self.my_id) or \
                   (entry["from_id"] == self.my_id and entry["to_id"] == self.current_chat):
                    chat_log.write(entry["formatted"])
```

- Clears the RichLog widget and filters `self.messages` based on the current mode:
  - **"all"**: only `type == "broadcast"` entries are shown.
  - **User-specific**: only `type == "private"` entries involving both `my_id` and `current_chat` are shown (regardless of direction).

```python
def update_sidebar(self):
    sidebar = self.query_one("#sidebar")
    for child in list(sidebar.children):
        if child.id and child.id.startswith("user_") and child.id != "user_all":
            child.remove()
    for uid in sorted(self.online_users, key=int):
        if uid == self.my_id:
            continue
        label = f"\u25cf User {uid}"
        sidebar.mount(Button(label, id=f"user_{uid}", classes="sidebar-btn"))
    self.update_sidebar_active()
```

- **Cleanup** — removes all dynamic user buttons (those matching `user_<id>`) while keeping the static "All" button and the divider.
- **Rebuild** — iterates over `online_users` sorted numerically, skips yourself, and mounts a new `Button` for each. The dot prefix (`\u25cf`) is the Unicode "●" character.
- **`update_sidebar_active()`** — sets the `variant` of each button to `"primary"` if it matches the current chat, or `"default"` otherwise. This gives visual feedback on which view is active.

### 5.11 Sending Messages

```python
def send_message(self):
    if not self._connected or not self.writer:
        return
    input_widget = self.query_one("#msg_input", Input)
    msg = input_widget.value.strip()
    if not msg:
        return
    input_widget.value = ""

    if self.current_chat == "all":
        self.writer.write((msg + "\n").encode())
    else:
        self.writer.write((f"/msg {self.current_chat} {msg}\n").encode())

    asyncio.create_task(self.writer.drain())
```

- Guards against sending when disconnected.
- If the current chat is "all", the raw message text is sent as-is → the server treats it as a broadcast.
- If chatting privately with a user, the message is prefixed with `/msg <target_id>` → the server routes it as a private message.
- **`drain()`** — flushes the write buffer. `writer.write()` buffers data in memory; `drain()` ensures it is actually written to the socket. This is called as a fire-and-forget `create_task` because we do not need to await the result synchronously.

### 5.12 User Actions

```python
def on_button_pressed(self, event):
    bid = event.button.id
    if bid == "send_btn":
        self.send_message()
    elif bid == "user_all":
        self.switch_chat("all")
    elif bid and bid.startswith("user_"):
        uid = bid.split("_", 1)[1]
        self.switch_chat(uid)

def on_input_submitted(self, event):
    if event.input.id == "msg_input":
        self.send_message()
```

- **`on_button_pressed`** — Textual's message handler for button press events. Dispatches based on the button's `id` attribute: the send button calls `send_message()`, the "All" button switches to broadcast view, and user buttons switch to private chat.
- **`on_input_submitted`** — triggered when the user presses Enter in the Input widget. Delegates to `send_message()`.

### 5.13 Quit

```python
def action_quit(self):
    if self.writer:
        try:
            self.writer.close()
        except Exception:
            pass
    self.exit()
```

- `action_quit` is the handler for the `ctrl+d` binding defined in `BINDINGS`. It closes the writer (which sends TCP FIN to the server) and calls `self.exit()` to shut down the Textual app.

---

## 6. Data Flow

### Connection Lifecycle

```
Client                          Server
  │                               │
  │  ─── TCP SYN ──────────────►  │
  │  ◄── TCP SYN-ACK ───────────  │
  │  ─── TCP ACK ──────────────►  │
  │                               │  server.accept()
  │                               │  next_id = 3
  │  ◄── "Your ID: 3\n" ────────  │
  │  ◄── "System: User 3 joined  │
  │        (total: 2)\n" ───────  │  broadcast()
  │  ◄── "USERLIST:1,2,3\n" ────  │  broadcast_user_list()
  │                               │
```

### Broadcast Flow

```
User 1                        Server                        User 2
  │                             │                             │
  │  ─── "hello\n" ──────────►  │                             │
  │                             │  handle_client()            │
  │                             │  broadcast("User 1: hello") │
  │  ◄── "User 1: hello\n" ───  │  ──── "User 1: hello\n" ──►│
  │                             │                             │
```

### Private Message Flow

```
User 1                        Server                        User 2
  │                             │                             │
  │  ─── "/msg 2 hey\n" ─────►  │                             │
  │                             │  handle_client()            │
  │                             │  send_private(1, 2, "hey") │
  │  ◄── "from 1 to 2: hey\n" ─ │  ── "from 1 to 2: hey\n" ─►│
  │                             │                             │
  │    (echo confirm)           │     (delivery)              │
```

### Disconnection Flow

```
Client                        Server
  │                             │
  │  ─── TCP FIN ────────────►  │
  │                             │  recv() returns b""
  │                             │  remove_client(conn)
  │                             │  broadcast("User 3 left...")
  │                             │  broadcast_user_list()
```

### TUI Client Chat Switching

```
User clicks "User 3" button
  │
  ├─► switch_chat("3")
  │     ├─ update_chat_header()    → "Chat: User 3 (private)"
  │     ├─ update_input_placeholder() → "Message User 3..."
  │     ├─ rebuild_chat_log()      → clear + show only 1↔3 private msgs
  │     └─ update_sidebar_active() → highlight User 3 button
  │
  ├─► User types and presses Enter
  │     └─ send_message()
  │           └─ writer.write("/msg 3 hello\n")
  │
  ├─► Server echoes back "from 1 to 3: hello"
  │     └─ handle_private_message()
  │           ├─ formatted as "[bold cyan]You -> User 3: hello[/bold cyan]"
  │           └─ displayed because current_chat == "3"
  │
  └─► User clicks "All" button
        └─ switch_chat("all")
              ├─ header → "Chat: All"
              └─ rebuild → shows all broadcast messages
```

---

*End of document.*
