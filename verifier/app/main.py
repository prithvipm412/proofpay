"""ProofPay verifier: FastAPI routes and process start (V-01..V-10).

Run (from verifier/):
    .venv/bin/python -m app.main            # 127.0.0.1:8000, one process, one worker (V-01)

Startup order: settings (V-03) -> chain ID and contract code -> START_BLOCK (V-CFG3) ->
data folder deployment ID (V-R5) -> signer lock in live mode (V-07) -> event history check
(V-E5) -> background loops (V-02). A failed step stops the process with a clear message.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .config import ConfigError, Settings, environment, load_settings
from .events import EventReader
from .images import ImageRejected, UploadError, decode_in_child, receive_upload
from .readiness import Readiness, SignerLockError, acquire_signer_lock
from .storage import DataFolderError, Storage, norm_hash

log = logging.getLogger("proofpay")

DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 8000  # section 3.1: port 8000


class StartupError(Exception):
    pass


@dataclass
class Services:
    settings: Settings
    storage: Storage
    chain: object
    reader: EventReader
    readiness: Readiness
    lock_fd: int | None
    decoder: Callable = decode_in_child

    def close(self) -> None:
        if self.lock_fd is not None:
            os.close(self.lock_fd)  # releases the flock
            self.lock_fd = None


def prepare(settings: Settings, chain, decoder: Callable = decode_in_child) -> Services:
    """All startup checks that must pass before the API serves (V-03, V-R5, V-07, V-CFG3, V-E5)."""
    try:
        chain_id = chain.chain_id()
    except Exception as exc:
        raise StartupError(f"Cannot reach MONAD_RPC_URL ({type(exc).__name__})")
    if chain_id != settings.chain_id:
        raise StartupError(f"RPC chain ID is {chain_id}, but CHAIN_ID is {settings.chain_id}")
    if not chain.code_exists():
        raise StartupError(f"No contract code at ESCROW_ADDRESS {settings.escrow_address}")
    latest = chain.latest_block_number()
    if settings.start_block > latest:
        raise StartupError(f"START_BLOCK {settings.start_block} is after the current block {latest} (V-CFG3)")

    storage = Storage(settings.data_dir, settings.deployment_id, settings.max_storage_mb, settings.start_block)
    storage.open()  # V-R5
    lock_fd = acquire_signer_lock(settings.data_dir) if settings.live else None  # V-07
    try:
        reader = EventReader(settings, storage, chain)
        reader.startup()  # V-E5
    except Exception:
        if lock_fd is not None:
            os.close(lock_fd)
        raise
    readiness = Readiness(settings, storage, chain, reader)
    return Services(settings, storage, chain, reader, readiness, lock_fd, decoder)


def create_app(services: Services, start_threads: bool = True) -> FastAPI:
    s = services.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        threads = []
        if start_threads:
            for name, target in (("event-reader", services.reader.run), ("readiness", services.readiness.run)):
                t = threading.Thread(target=target, name=name, daemon=True)
                t.start()
                threads.append(t)
        log.info("Verifier started: mode=%s deployment=%s data=%s", s.mode, s.deployment_id, s.data_dir)
        try:
            yield
        finally:
            services.reader.stop()
            services.readiness.stop()
            for t in threads:
                t.join(timeout=15)
            services.close()

    app = FastAPI(title="ProofPay verifier", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,  # V-05. CORS is not access control.
        allow_origins=list(s.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")  # V-08
    def health():
        snap = services.readiness.snapshot()
        return JSONResponse(snap, status_code=200 if snap["ready"] else 503)

    @app.post("/upload")  # V-09
    async def upload(request: Request):
        if datetime.now(timezone.utc) >= s.admission_until:  # V-S9
            return JSONResponse({"error": "Uploads are closed for this demo"}, status_code=410)
        try:
            with services.storage.admit_upload():  # V-S7
                received = await receive_upload(  # V-L1
                    request.stream(),
                    request.headers.get("content-type"),
                    request.headers.get("content-length"),
                    services.storage.tmp,
                )
                sha = await run_in_threadpool(services.storage.store_upload, received, services.decoder)
        except UploadError as exc:
            return JSONResponse({"error": exc.message}, status_code=exc.status)
        except ImageRejected as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"sha256": sha}

    @app.get("/files/{sha256}")  # V-10
    def files(sha256: str):
        try:
            path = services.storage.preview_path(norm_hash(sha256))
        except ValueError:
            return JSONResponse({"error": "Not found"}, status_code=404)
        if not path.exists():
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(
            path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"}
        )

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="ProofPay verifier")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        settings = load_settings(environment())
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    from .chain import Chain

    try:
        services = prepare(settings, Chain(settings.rpc_url, settings.escrow_address))
    except (StartupError, DataFolderError, SignerLockError, ConfigError) as exc:
        print(f"Startup stopped: {exc}", file=sys.stderr)
        return 2

    import uvicorn

    uvicorn.run(create_app(services), host=args.host, port=args.port, workers=1, log_level="info")  # V-01
    return 0


if __name__ == "__main__":
    sys.exit(main())
