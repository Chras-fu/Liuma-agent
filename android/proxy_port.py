import argparse
import socket, threading


class PipeThread(threading.Thread):

    def __init__(self, source_socket, target_socket):
        threading.Thread.__init__(self)
        self.source = source_socket
        self.target = target_socket

    def run(self):
        while True:
            try:
                data = self.source.recv(1024)
                if not data:
                    break
                self.target.send(data)
            except:
                break


class Forwarding(threading.Thread):

    def __init__(self, local_port, target_port):
        threading.Thread.__init__(self)
        self.local_port = local_port
        self.target_port = target_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("0.0.0.0", local_port))
        self.sock.listen(10)

    def run(self):
        while True:
            client_fd, _ = self.sock.accept()
            target_fd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_fd.connect(("127.0.0.1", self.target_port))
            # two direct pipe
            PipeThread(target_fd, client_fd).start()
            PipeThread(client_fd, target_fd).start()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-lp",
                        "--local_port",
                        help="local port")
    parser.add_argument("-tp",
                        "--target_port",
                        type=int,
                        help="target port")
    args = parser.parse_args()
    local_port = int(args.local_port)
    target_port = int(args.target_port)
    Forwarding(local_port, target_port).start()

