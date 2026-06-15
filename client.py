
import socket

def run_client():
    # The server's hostname or IP address and port
    host = '127.0.0.1'
    port = 65432        

    # Create a TCP socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
        # Connect to the server
        client_socket.connect((host, port))
        
        # Send a message (must be encoded to bytes)
        message = "Hello, Python Socket Server!"
        client_socket.sendall(message.encode('utf-8'))
        
        # Read the server's response
        data = client_socket.recv(1024)
        print(f"Received from server: {data.decode('utf-8')}")

if __name__ == "__main__":
    run_client()
