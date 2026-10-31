"""Docker health check; sends traffic only through the local SOCKS5 relay."""

try:
    from .relay import probe_local
except ImportError:
    from relay import probe_local


def main():
    try:
        probe_local()
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
