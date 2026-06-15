
import socket

def run_server():
    # Define local host and a non-privileged port
    host = '127.0.0.1' 
    port = 65432        

    # Create a TCP socket using a context manager
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        # Bind the socket to the address and port
        server_socket.bind((host, port))
        
        # Listen for connections
        server_socket.listen()
        print(f"Server is listening on {host}:{port}...")
        
        # Accept a connection (blocks until a client connects)
        conn, addr = server_socket.accept()
        with conn:
            print(f"Connected successfully by {addr}")
            while True:
                # Receive data from the client (up to 1024 bytes)
                data = conn.recv(1024)
                if not data:
                    break # Client closed connection
                
                print(f"Received from client: {data.decode('utf-8')}")
                
                # Echo data back to client
                conn.sendall(data)

if __name__ == "__main__":
    run_server()
