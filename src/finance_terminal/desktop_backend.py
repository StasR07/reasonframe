"""Local-only entry point used by the desktop sidecar."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("port must be in the range 1..65535")
    os.environ["FINANCE_TERMINAL_DATA_DIR"] = args.data_dir
    log_path = Path(args.data_dir) / "logs" / "provider-runtime.log"
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    logging.basicConfig(
        filename=log_path, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        log_path.chmod(0o600)
    except OSError:
        pass
    import uvicorn
    uvicorn.run(
        "finance_terminal.api:app", host="127.0.0.1", port=args.port,
        access_log=False, server_header=False, proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
