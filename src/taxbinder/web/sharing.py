"""Shared access on the home network: paired devices, pairing codes, a self-signed certificate and
the network listener (§11.1, §12.2).

The app window keeps its loopback connection. Sharing adds a second, HTTPS-only listener on the
LAN that only paired devices can use. Pairing is a 6-digit code shown on this Mac, valid for
10 minutes and 5 tries; the device then holds a long-lived cookie whose hash is kept in the
vault's `private/` folder (never in the git-tracked records).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import logging
import re
import secrets
import socket
import subprocess
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("taxbinder.sharing")

DEFAULT_PORT = 8765


def configured_port() -> int:
    """`TAXBINDER_LAN_PORT`, else 8765 (another app may already use it)."""
    import os

    return int(os.environ.get("TAXBINDER_LAN_PORT") or DEFAULT_PORT)


PAIRING_MINUTES = 10
PAIRING_TRIES = 5
DEVICE_COOKIE = "tp_device"


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def device_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "device"


# -- devices ------------------------------------------------------------------------------


@dataclass
class Device:
    id: str
    name: str
    token_sha256: str
    created: str
    last_seen: str | None = None


@dataclass
class Devices:
    """Paired devices, in `<vault>/private/devices.json` (token hashes only)."""

    path: Path
    items: list[Device] = field(default_factory=list)
    _seen_saved: dict[str, dt.datetime] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Devices:
        items = []
        if path.exists():
            items = [Device(**d) for d in json.loads(path.read_text())]
        return cls(path, items)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(d) for d in self.items], indent=2))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def add(self, name: str) -> tuple[Device, str]:
        """A new device and its cookie token (returned once, stored only as a hash)."""
        token = secrets.token_urlsafe(32)
        device = Device(
            id=secrets.token_hex(6),
            name=name.strip()[:60] or "Device",
            token_sha256=_hash(token),
            created=now().isoformat(timespec="seconds"),
        )
        self.items.append(device)
        self.save()
        return device, token

    def check(self, token: str | None) -> Device | None:
        if not token:
            return None
        digest = _hash(token)
        for d in self.items:
            if secrets.compare_digest(d.token_sha256, digest):
                self._touch(d)
                return d
        return None

    def _touch(self, d: Device) -> None:
        current = now()
        d.last_seen = current.isoformat(timespec="seconds")
        last = self._seen_saved.get(d.id)
        if last is None or current - last > dt.timedelta(minutes=10):  # not on every request
            self._seen_saved[d.id] = current
            self.save()

    def revoke(self, device_id: str) -> bool:
        before = len(self.items)
        self.items = [d for d in self.items if d.id != device_id]
        if len(self.items) != before:
            self.save()
            return True
        return False


# -- pairing ------------------------------------------------------------------------------


@dataclass
class Pairing:
    code: str
    expires: dt.datetime
    tries_left: int = PAIRING_TRIES

    @classmethod
    def new(cls) -> Pairing:
        code = f"{secrets.randbelow(1_000_000):06d}"
        return cls(code, now() + dt.timedelta(minutes=PAIRING_MINUTES))

    def try_code(self, code: str) -> str:
        """'ok', 'wrong' (tries left), or 'closed' (expired or out of tries)."""
        if self.tries_left <= 0 or now() > self.expires:
            return "closed"
        if secrets.compare_digest(re.sub(r"\D", "", code or ""), self.code):
            self.tries_left = 0  # single use
            return "ok"
        self.tries_left -= 1
        return "wrong" if self.tries_left > 0 else "closed"


# -- identity and certificate -------------------------------------------------------------


def local_host_name() -> str:
    """This Mac's Bonjour name, e.g. `alexs-macbook` (reachable as `<name>.local`)."""
    try:
        out = subprocess.run(
            ["/usr/sbin/scutil", "--get", "LocalHostName"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return socket.gethostname().removesuffix(".local")


def lan_ip() -> str | None:
    """The address other devices on the network reach this Mac at (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET; connect() on UDP only picks the route
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith("127.") else ip


def ensure_certificate(folder: Path, host: str, ip: str | None) -> tuple[Path, Path, str]:
    """Self-signed certificate for `<host>.local` (and the LAN IP); reused while it still fits.

    Returns (certificate, key, SHA-256 fingerprint as `AB:CD:…`). The key file is 0600 in the
    vault's `private/` folder.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder / "lan-cert.pem", folder / "lan-key.pem"
    names = {f"{host}.local".lower()}
    ips = {ip} if ip else set()

    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have_names = {n.lower() for n in san.get_values_for_type(x509.DNSName)}
        have_ips = {str(a) for a in san.get_values_for_type(x509.IPAddress)}
        fresh = cert.not_valid_after_utc > now() + dt.timedelta(days=30)
        if fresh and names <= have_names and ips <= have_ips:
            return cert_path, key_path, _fingerprint(cert)

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{host}.local")])
    alt = [x509.DNSName(n) for n in sorted(names)] + [
        x509.IPAddress(ipaddress.ip_address(a)) for a in sorted(ips)
    ]
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now() - dt.timedelta(minutes=5))
        .not_valid_after(now() + dt.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, _fingerprint(cert)


def _fingerprint(cert: Any) -> str:  # noqa: ANN401
    from cryptography.hazmat.primitives import hashes

    return ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256()))


# -- the network listener ----------------------------------------------------------------

LAN_SCOPE_KEY = "taxbinder.lan"


def lan_app(app: Any) -> Any:  # noqa: ANN401
    """The app as served on the network: every request is labelled as coming from the LAN.

    The guard decides by this label, never by port numbers or addresses.
    """

    async def labelled(scope: dict, receive: Any, send: Any) -> None:  # noqa: ANN401
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, LAN_SCOPE_KEY: True}
        await app(scope, receive, send)

    return labelled


@dataclass
class Sharing:
    port: int
    host: str  # Bonjour name, without .local
    ip: str | None
    fingerprint: str
    server: Any = None  # uvicorn.Server
    thread: threading.Thread | None = None

    @property
    def hosts(self) -> set[str]:
        """Host headers accepted on the network listener (DNS-rebinding guard)."""
        hs = {f"{self.host}.local:{self.port}".lower()}
        if self.ip:
            hs.add(f"{self.ip}:{self.port}")
        return hs

    @property
    def origins(self) -> set[str]:
        return {f"https://{h}" for h in self.hosts}

    @property
    def address(self) -> str:
        return f"https://{self.host}.local:{self.port}"

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)


def _listen_socket(port: int) -> socket.socket:
    """One socket for IPv6 and IPv4 on every interface.

    `<name>.local` resolves to IPv6 addresses too, and browsers try them; with the macOS
    firewall's stealth mode, a port nobody listens on doesn't refuse, it hangs. So listen on both.
    """
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)  # IPv4 too (::ffff:a.b.c.d)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("::", port))
    except OSError:  # no IPv6 on this Mac: IPv4 only
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))  # noqa: S104 - the point: other devices on the LAN
    sock.listen(64)
    return sock


def start(app: Any, private: Path, port: int = DEFAULT_PORT) -> Sharing:  # noqa: ANN401
    """Start the HTTPS listener on all interfaces; raises OSError if the port is taken."""
    import uvicorn

    host, ip = local_host_name(), lan_ip()
    cert, key, fingerprint = ensure_certificate(private / "tls", host, ip)
    sock = _listen_socket(port)
    port = sock.getsockname()[1]  # port 0 (tests) means "any free port"
    config = uvicorn.Config(
        lan_app(app),
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
        log_config=None,
        access_log=False,  # URLs never reach the log
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, name="lan-listener", daemon=True
    )
    thread.start()
    log.info("sharing on the network at port %d", port)
    return Sharing(port, host, ip, fingerprint, server, thread)
