#!/usr/bin/env python3
"""生成 nginx 用的自签 TLS 证书（首次安装 / 证书缺失时）。

为什么单独一个脚本（2026-11-19 安装包最小化时新增）：
  发布包里**刻意不带** `*.key` / `*.crt` —— 私钥不能随包分发（AGENTS.md 四.2）。
  但 nginx.conf.template 无条件 `ssl_certificate {{NGINX_CERTS}}/portal.crt`，
  没有证书 nginx 起不来。目标机首次安装时需要就地生成一份「只给本机用」的
  自签证书，这就是本脚本的唯一职责。

依赖：`cryptography`（已在 apps/my_app/requirements.lock.txt 里，随 wheels 离线安装）。
      不要改成 openssl 子进程 —— Windows 默认没有 openssl，Git 的 openssl 又不在 PATH。

用法（在部署根执行）：
  runtime\\python312\\python.exe ops\\deploy\\make-selfsigned-cert.py
  runtime\\python312\\python.exe ops\\deploy\\make-selfsigned-cert.py --dir nginx-1.28.0\\certs --force

退出码：0 = 已有证书或生成成功；1 = 环境不满足（缺 cryptography / 写不进去 / 未给 --dir 且找不到 nginx）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import ipaddress
import socket
import sys
from pathlib import Path

# Windows 上 Python 的 stdout 默认是系统 ANSI（如 cp1252），打印中文会
# UnicodeEncodeError 直接让脚本在「证书已生成」之后崩掉。统一转 UTF-8；
# 控制台若仍是老代码页会显示乱码，但不影响退出码与文件。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 —— 老 Python / 被重定向时不强求
        pass


def _default_cert_dir(root: Path) -> Path:
    """与 ops/deploy/noc-paths.sh 的 _np_resolve_nginx_dir 同序：先版本根，再 runtime/。

    每个候选目录按 sbin/nginx、nginx、nginx.exe 依次探测：Linux 随包 nginx
    的二进制在 sbin/ 下（runtime-build.yml 编译后断言 sbin/nginx 存在），
    Windows 的在版本目录根（nginx.exe）。只认直接子文件会漏掉 sbin，
    导致证书写到 <根>\\nginx-1.28.0\\certs、nginx 却从 runtime\\nginx-1.28.0
    读 certs 的错位（2026-11-19 安装包实测风险点；v0.0.3 P1-8 复现，#202）。

    两处都找不到时抛错，不再静默回退到根目录——写错地方比报错更糟。
    """
    for base in (root / "nginx-1.28.0", root / "runtime" / "nginx-1.28.0"):
        for name in ("sbin/nginx", "nginx", "nginx.exe"):
            if (base / name).exists():
                return base / "certs"
    raise FileNotFoundError(
        "找不到 nginx：已查 "
        f"{root / 'nginx-1.28.0'} 与 {root / 'runtime' / 'nginx-1.28.0'} "
        "（各查 sbin/nginx、nginx、nginx.exe）；请用 --dir 指定证书目录"
    )


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="生成 nginx 自签证书（portal.crt / portal.key）")
    parser.add_argument(
        "--dir",
        default=None,
        help="证书输出目录（默认按 noc-paths.sh 的顺序找 nginx 目录下的 certs/）",
    )
    parser.add_argument("--days", type=int, default=3650, help="有效期天数（默认 3650）")
    parser.add_argument("--force", action="store_true", help="已存在也覆盖")
    args = parser.parse_args()

    if args.dir is None:
        try:
            out = _default_cert_dir(root)
        except FileNotFoundError as exc:
            print(f"[cert] {exc}", file=sys.stderr)
            return 1
    else:
        out = Path(args.dir).resolve()
    crt, key = out / "portal.crt", out / "portal.key"
    if crt.exists() and key.exists() and not args.force:
        print(f"[cert] 已存在，跳过：{crt}")
        print("[cert] 如需重新生成，加 --force（会让已信任旧证书的客户端需要重新信任）")
        return 0

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        print(
            "[cert] 缺 cryptography。先装依赖：\n"
            "  runtime\\python312\\python.exe -m pip install --no-index --find-links wheels "
            "-r apps\\my_app\\requirements.lock.txt",
            file=sys.stderr,
        )
        return 1

    host = socket.gethostname()
    san: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    if host:
        san.append(x509.DNSName(host))

    key_obj = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NOC SOC Intelligent Platform"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key_obj.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=args.days))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key_obj, hashes.SHA256())
    )

    out.mkdir(parents=True, exist_ok=True)
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(
        key_obj.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        key.chmod(0o600)
    except OSError:
        pass  # Windows 上没有 POSIX 权限位；目录本身应在受控盘符内

    print(f"[cert] 已生成（{args.days} 天，SAN: {', '.join(str(s.value) for s in san)}）")
    print(f"[cert]   证书：{crt}")
    print(f"[cert]   私钥：{key}")
    print("[cert] 浏览器首次访问会提示「不安全」，属自签证书预期；确认无误后加入信任即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
