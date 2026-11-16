"""Fixed-cost scrypt hashes; optional private-file setup without echoed passwords."""
from dataclasses import dataclass, field
import hashlib
import hmac
import os
import re
import secrets

_PATTERN = re.compile(r"scrypt\$131072\$8\$1\$[0-9a-f]{32}\$[0-9a-f]{64}")


def validate_password_hash(value):
    if not isinstance(value, str) or _PATTERN.fullmatch(value) is None:
        raise ValueError("Invalid demo password hash configuration")
    return value


def _derive(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=131072, r=8, p=1,
                          dklen=32, maxmem=256 * 1024 * 1024)


def hash_password(password):
    if not isinstance(password, str) or not 1 <= len(password) <= 256 or not password.strip():
        raise ValueError("Invalid password")
    salt = secrets.token_bytes(16)
    return "scrypt$131072$8$1$" + salt.hex() + "$" + _derive(password, salt).hex()


def verify_password(password, encoded):
    validate_password_hash(encoded)
    salt, expected = encoded.split("$")[-2:]
    return hmac.compare_digest(_derive(password, bytes.fromhex(salt)), bytes.fromhex(expected))


@dataclass(frozen=True)
class DemoCredentials:
    acme_hash: str = field(repr=False)
    globex_hash: str = field(repr=False)

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(validate_password_hash(env.get("DEMO_ACME_PASSWORD_HASH")),
                   validate_password_hash(env.get("DEMO_GLOBEX_PASSWORD_HASH")))


def main():
    import argparse
    import getpass
    import sys
    import warnings
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="New private env file; never overwrite")
    args = parser.parse_args()
    # Refuse an existing target before requesting any passwords.
    if os.path.lexists(args.output):
        raise SystemExit("Output file already exists")
    if not sys.stdin.isatty():
        raise SystemExit("Interactive terminal required")
    values = []
    for account, key in (("acme-demo", "DEMO_ACME_PASSWORD_HASH"), ("globex-demo", "DEMO_GLOBEX_PASSWORD_HASH")):
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                password = getpass.getpass(account + " password: ")
                confirmation = getpass.getpass("Confirm password: ")
            except (getpass.GetPassWarning, EOFError):
                raise SystemExit("Secure interactive input unavailable") from None
        if password != confirmation:
            raise SystemExit("Passwords do not match")
        try:
            values.append((key, hash_password(password)))
        except (ValueError, UnicodeError):
            raise SystemExit("Invalid password") from None
        del password, confirmation
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        for key, value in values:
            # Single quotes prevent Compose dotenv interpolation of the '$' format.
            stream.write(key + "='" + value + "'\n")
    print("Private password hash file created; no credentials displayed")


if __name__ == "__main__":
    main()
