import asyncio
import base64
import re

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key
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

    #sidebar_scroll {
        height: 1fr;
        overflow-y: auto;
    }

    #sidebar_divider {
        height: 1;
        padding: 0 1;
        color: $text-disabled;
    }

    #chat_area {
        width: 1fr;
        height: 1fr;
    }

    #chat_header {
        height: 1;
        background: $panel;
        padding: 0 1;
        text-style: bold;
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

    #server_status {
        width: 25;
    }

    #client_count {
        width: 20;
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

    def __init__(self, host="127.0.0.1", port=65432):
        super().__init__()
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self._connected = False
        self.my_id = None
        self._connecting = False
        self._shutdown = False
        self._listen_task = None
        self.messages = []
        self.current_chat = "all"
        self.online_users = set()
        self._key_sent = False

        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        pubkey_pem = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.pubkey_b64 = base64.b64encode(pubkey_pem).decode()
        self.peer_public_keys = {}

    def encrypt_text(self, pubkey, plaintext):
        ciphertext = pubkey.encrypt(
            plaintext.encode(),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return base64.b64encode(ciphertext).decode()

    def decrypt_text(self, encrypted_b64):
        try:
            ciphertext = base64.b64decode(encrypted_b64)
            plaintext = self.private_key.decrypt(
                ciphertext,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            return plaintext.decode()
        except Exception:
            return None

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
                yield Static("-- Online --", id="sidebar_divider")

            with Vertical(id="chat_area"):
                yield Static("Chat: All", id="chat_header")
                yield RichLog(id="chat_log", highlight=True, markup=True, wrap=True, max_lines=1000)
                yield Horizontal(
                    Input(placeholder="Message everyone...", id="msg_input"),
                    Button("Send", id="send_btn", variant="primary"),
                    id="input_row",
                )

        yield Footer()

    def on_mount(self):
        self._shutdown = False
        asyncio.create_task(self.connect())

    def on_unmount(self):
        self._shutdown = True

    async def connect(self):
        if self._connecting:
            return
        self._connecting = True
        self._key_sent = False
        self.log_message(f"[yellow]Connecting to {self.host}:{self.port}...[/yellow]")
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            self._connected = True
            self._connecting = False
            self.set_connection_status(True)
            self.log_message("[bold green]Connected to server[/bold green]")
            self.query_one("#msg_input", Input).disabled = False
            self.query_one("#msg_input", Input).focus()
            self._listen_task = asyncio.create_task(self.listen())

        except (ConnectionRefusedError, OSError) as e:
            self._connected = False
            self._connecting = False
            self.set_connection_status(False)
            self.log_message(f"[red]Connection failed: {e}[/red]")
            self.query_one("#msg_input", Input).disabled = True
            self.query_one("#client_count", Static).update("")
            if self._shutdown:
                return
            await asyncio.sleep(5)
            asyncio.create_task(self.connect())

    def send_key(self):
        if self.writer and not self._key_sent:
            self.writer.write((f"__key__ {self.pubkey_b64}\n").encode())
            asyncio.create_task(self.writer.drain())
            self._key_sent = True

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
                elif msg.startswith("__key__"):
                    self.handle_key_message(msg)
                else:
                    self.handle_broadcast(msg)

        except asyncio.CancelledError:
            return

        except Exception as e:
            self.log_message(f"[red]Connection lost: {e}[/red]")

        self._connected = False
        self.set_connection_status(False)
        try:
            self.query_one("#msg_input", Input).disabled = True
        except Exception:
            pass
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        if not self._shutdown and not self._connecting:
            self.messages.clear()
            self.current_chat = "all"
            self.online_users.clear()
            self.peer_public_keys.clear()
            self._key_sent = False
            self.update_sidebar()
            self.update_chat_header()
            asyncio.create_task(self.connect())

    def handle_system_message(self, msg):
        content = msg[len("__system__"):].strip()
        if content.startswith("Your ID:"):
            self.my_id = content.split(":")[1].strip()
            self.log_message(f"[bold cyan]You are User {self.my_id}[/bold cyan]")
            self.send_key()
        elif content.startswith("USERLIST:"):
            ids_str = content.split(":", 1)[1].strip()
            self.online_users = set(ids_str.split(",")) if ids_str else set()
            self.update_sidebar()
        elif content.startswith("User ") and "is not online" in content:
            self.log_message(f"[red]{content}[/red]")

    def handle_key_message(self, msg):
        rest = msg[len("__key__"):].strip()
        match = re.match(r'^from\s+(\d+):\s*(.*)', rest)
        if match:
            user_id = match.group(1)
            pubkey_b64 = match.group(2)
            try:
                pubkey_pem = base64.b64decode(pubkey_b64)
                pubkey = load_pem_public_key(pubkey_pem)
                self.peer_public_keys[user_id] = pubkey
            except Exception:
                pass

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

    def handle_private_message(self, msg):
        rest = msg[len("__private__"):].strip()
        match = re.match(r'^from\s+(\d+)\s+to\s+(\d+):\s*(.*)', rest)
        if not match:
            return
        from_id = match.group(1)
        to_id = match.group(2)
        text = match.group(3)

        if from_id == self.my_id:
            return

        display_text = text
        if text.startswith("__enc__"):
            encrypted_b64 = text[len("__enc__"):]
            decrypted = self.decrypt_text(encrypted_b64)
            if decrypted is not None:
                display_text = decrypted
            else:
                display_text = "[red][decryption failed][/red]"

        formatted = f"[bold yellow]User {from_id} (private): {display_text}[/bold yellow]"
        chat_partner = from_id

        entry = {
            "type": "private",
            "from_id": from_id,
            "to_id": to_id,
            "formatted": formatted,
        }
        self.messages.append(entry)

        if self.current_chat == chat_partner:
            self.query_one("#chat_log", RichLog).write(formatted)

    def set_connection_status(self, connected):
        try:
            static = self.query_one("#server_status", Static)
        except Exception:
            return
        if connected:
            static.update("[green]\u25cf Server: Connected[/green]")
        else:
            static.update("[red]\u25cf Server: Disconnected[/red]")

    def log_message(self, msg):
        try:
            self.query_one("#chat_log", RichLog).write(msg)
        except Exception:
            pass

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
            asyncio.create_task(self.writer.drain())
            return

        recipient_id = self.current_chat
        pubkey = self.peer_public_keys.get(recipient_id)

        if pubkey:
            encrypted = self.encrypt_text(pubkey, msg)
            self.writer.write((f"/msg {recipient_id} __enc__{encrypted}\n").encode())
        else:
            self.writer.write((f"/msg {recipient_id} {msg}\n").encode())

        asyncio.create_task(self.writer.drain())

        formatted = f"[bold cyan]You -> User {recipient_id}: {msg}[/bold cyan]"
        self.messages.append({
            "type": "private",
            "from_id": self.my_id,
            "to_id": recipient_id,
            "formatted": formatted,
        })
        if self.current_chat == recipient_id:
            self.query_one("#chat_log", RichLog).write(formatted)

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

    def update_chat_header(self):
        header = self.query_one("#chat_header", Static)
        if self.current_chat == "all":
            header.update("Chat: All")
        else:
            header.update(f"\U0001f512 Chat: User {self.current_chat} (private) \u00b7 End-to-end encrypted")

    def update_input_placeholder(self):
        inp = self.query_one("#msg_input", Input)
        if self.current_chat == "all":
            inp.placeholder = "Message everyone..."
        else:
            inp.placeholder = f"Message User {self.current_chat}..."

    def rebuild_chat_log(self):
        chat_log = self.query_one("#chat_log", RichLog)
        chat_log.clear()
        if self.current_chat != "all":
            chat_log.write("[bold green]\U0001f512 Messages are end-to-end encrypted[/bold green]")
        for entry in self.messages:
            if self.current_chat == "all":
                if entry["type"] == "broadcast":
                    chat_log.write(entry["formatted"])
            else:
                if entry["type"] == "private":
                    if (entry["from_id"] == self.current_chat and entry["to_id"] == self.my_id) or \
                       (entry["from_id"] == self.my_id and entry["to_id"] == self.current_chat):
                        chat_log.write(entry["formatted"])

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

    def update_sidebar_active(self):
        sidebar = self.query_one("#sidebar")
        for child in sidebar.children:
            if not hasattr(child, "id") or not child.id:
                continue
            if child.id == "user_all":
                child.variant = "primary" if self.current_chat == "all" else "default"
            elif child.id and child.id.startswith("user_"):
                uid = child.id.split("_", 1)[1]
                child.variant = "primary" if uid == self.current_chat else "default"

    def action_quit(self):
        self._shutdown = True
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass
        self.exit()


if __name__ == "__main__":
    app = ChatTUI()
    app.run()
