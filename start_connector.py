"""
Migration Intelligence Connector — Startup Script
===================================================
Starts the FastAPI REST server that exposes all Migration Validator tools
to Gemini Enterprise.

Usage:
    python start_connector.py [--port 8001] [--reload]
    # or via uvicorn directly:
    uvicorn src.gemini_connector.api:app --port 8001 --reload

Environment variables required:
    GOOGLE_API_KEY or GEMINI_API_KEY  — for Gemini function-calling
    CONNECTOR_API_TOKEN               — optional bearer token for write endpoints
    (plus all existing Migration Validator .env variables)

The connector listens on http://localhost:8001 by default.
Open http://localhost:8001/docs for the interactive API explorer.
"""

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).parent / "src"
_ROOT = Path(__file__).parent
for _p in (str(_SRC), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")


def main():
    parser = argparse.ArgumentParser(description="Migration Intelligence Connector API server")
    parser.add_argument("--host",   default="0.0.0.0",  help="Bind host")
    parser.add_argument("--port",   default=8001, type=int, help="Bind port")
    parser.add_argument("--reload", action="store_true",   help="Auto-reload on code changes")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed. Run: pip install uvicorn[standard]")
        sys.exit(1)

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║     Migration Intelligence Connector — REST API              ║
║                                                              ║
║  URL:     http://{args.host}:{args.port}                           ║
║  Docs:    http://localhost:{args.port}/docs                        ║
║  Health:  http://localhost:{args.port}/health                      ║
║  Tools:   http://localhost:{args.port}/tools                       ║
║                                                              ║
║  Register /tools endpoint in Gemini Enterprise connector     ║
║  to enable all 23 migration tools.                           ║
╚══════════════════════════════════════════════════════════════╝
""")

    uvicorn.run(
        "src.gemini_connector.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
