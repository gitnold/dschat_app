import asyncio
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

    def compose(self):
        yield Header(show_clock=True)
        yield Horizontal(
            Static("", id="server_status"),
            Static("", id="client_count"),
            id="status_row",
        )
        yield RichLog(id="chat_log", highlight=True, markup=True, wrap=True, max_lines=1000)
        yield Horizontal(
            Input(placeholder="Type a message and press Enter...", id="msg_input"),
            Button("Send", id="send_btn", variant="primary"),
            id="input_row",
        )
        yield Footer()

    def on_mount(self):
        asyncio.create_task(self.connect())

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

    async def listen(self):
        try:
            while True:
                data = await self.reader.readline()
                if not data:
                    break
                msg = data.decode().strip()
                if msg.startswith("__system__"):
                    self.handle_system_message(msg)
                else:
                    self.log_message(msg)
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
            await asyncio.sleep(3)
            if not self._connecting:
                asyncio.create_task(self.connect())

    def handle_system_message(self, msg):
        content = msg[len("__system__"):].strip()
        if content.startswith("Your ID:"):
            self.my_id = content.split(":")[1].strip()
            self.log_message(f"[bold cyan]You are User {self.my_id}[/bold cyan]")
        elif content.startswith("CLIENTS:"):
            count = content.split(":")[1].strip()
            self.query_one("#client_count", Static).update(f"Clients: {count}")

    def set_connection_status(self, connected):
        static = self.query_one("#server_status", Static)
        if connected:
            static.update("[green]● Server: Connected[/green]")
        else:
            static.update("[red]● Server: Disconnected[/red]")

    def log_message(self, msg):
        self.query_one("#chat_log", RichLog).write(msg)

    def on_button_pressed(self, event):
        if event.button.id == "send_btn":
            self.send_message()

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
        self.writer.write((msg + "\n").encode())
        asyncio.create_task(self.writer.drain())

    def action_quit(self):
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass
        self.exit()


if __name__ == "__main__":
    app = ChatTUI()
    app.run()
