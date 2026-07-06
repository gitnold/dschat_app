import base64
import re
import socket
import threading

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key


def encrypt_text(pubkey, plaintext):
    ciphertext = pubkey.encrypt(
        plaintext.encode(),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode()


def decrypt_text(private_key, encrypted_b64):
    try:
        ciphertext = base64.b64decode(encrypted_b64)
        plaintext = private_key.decrypt(
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


def receive(conn, private_key, peer_keys):
    buffer = ""
    my_id = None
    while True:
        try:
            data = conn.recv(4096)
            if not data:
                break
            buffer += data.decode()
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                if line.startswith("__system__"):
                    content = line[len("__system__"):].strip()
                    if content.startswith("Your ID:"):
                        my_id = content.split(":")[1].strip()
                        print(f"[You are User {my_id}]")
                    continue

                if line.startswith("__key__"):
                    rest = line[len("__key__"):].strip()
                    m = re.match(r'^from\s+(\d+):\s*(.*)', rest)
                    if m:
                        try:
                            pubkey_pem = base64.b64decode(m.group(2))
                            peer_keys[m.group(1)] = load_pem_public_key(pubkey_pem)
                        except Exception:
                            pass
                    continue

                if line.startswith("__private__"):
                    rest = line[len("__private__"):].strip()
                    m = re.match(r'^from\s+(\d+)\s+to\s+(\d+):\s*(.*)', rest)
                    if m and m.group(2) == my_id:
                        text = m.group(3)
                        if text.startswith("__enc__"):
                            decrypted = decrypt_text(private_key, text[len("__enc__"):])
                            if decrypted is not None:
                                text = decrypted
                            else:
                                text = "[decryption failed]"
                        print(f"User {m.group(1)} (private): {text}")
                    continue

                print(line)
        except Exception:
            break


def run_client(host="127.0.0.1", port=65432):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pubkey_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pubkey_b64 = base64.b64encode(pubkey_pem).decode()
    peer_keys = {}
    key_sent = False

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, port))
        print(f"Connected to {host}:{port}")

        threading.Thread(target=receive, args=(s, private_key, peer_keys), daemon=True).start()

        while True:
            try:
                msg = input()
                if msg.lower() in ("/quit", "/exit"):
                    break

                if not key_sent:
                    s.sendall((f"__key__ {pubkey_b64}\n").encode())
                    key_sent = True

                parts = msg.split(maxsplit=2)
                if len(parts) >= 3 and parts[0] == "/msg":
                    target = parts[1]
                    text = parts[2]
                    pubkey = peer_keys.get(target)
                    if pubkey:
                        encrypted = encrypt_text(pubkey, text)
                        s.sendall((f"/msg {target} __enc__{encrypted}\n").encode())
                    else:
                        s.sendall((msg + "\n").encode())
                else:
                    s.sendall((msg + "\n").encode())
            except (EOFError, KeyboardInterrupt):
                break


if __name__ == "__main__":
    run_client()
