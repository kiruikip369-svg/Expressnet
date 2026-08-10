import smtplib
import socket

from django.core.mail.backends.smtp import EmailBackend


class IPv4SMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        last_error = None
        for family, socktype, proto, _, address in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
            sock = socket.socket(family, socktype, proto)
            try:
                sock.settimeout(timeout)
                if self.source_address:
                    sock.bind(self.source_address)
                sock.connect(address)
                return sock
            except OSError as exc:
                last_error = exc
                sock.close()
        if last_error:
            raise last_error
        raise OSError(f"No IPv4 address found for SMTP host {host}")


class IPv4EmailBackend(EmailBackend):
    connection_class = IPv4SMTP
