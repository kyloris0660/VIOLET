import argparse
import os
import sys

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    if args.debug:
        os.environ["BLOMBOORU_DEBUG"] = "true"

    from backend.app.utils.logger import logger
    from backend.app.config import settings, APP_VERSION

    logger.info("Starting V.I.O.L.E.T." + (" with debug mode enabled" if args.debug else ""))
    logger.info(
        "Environment: VIOLET_ENV=%s | APP_VERSION=%s | CODE_ROOT=%s | STORAGE_ROOT=%s | DB_NAME=%s",
        settings.VIOLET_ENV,
        APP_VERSION,
        settings.CODE_ROOT,
        settings.STORAGE_ROOT,
        settings.DB_NAME,
    )
    logger.info(
        "Python runtime: executable=%s | version=%s.%s.%s | is_venv=%s",
        sys.executable,
        sys.version_info.major, sys.version_info.minor, sys.version_info.micro,
        sys.prefix != sys.base_prefix,
    )
    if settings.WORKTREE_PATH:
        logger.info("Running from git worktree: %s", settings.WORKTREE_PATH)

    port = int(os.getenv("APP_PORT", 8000))
    uvicorn.run(
        "backend.app.main:app",
        host="0.0.0.0",
        port=port,
        reload=args.debug,
        log_config=None,
    )
