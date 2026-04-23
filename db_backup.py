"""
SQLite DB backup/restore via Azure Blob Storage.
Uses storage account connection string from env var AZURE_STORAGE_CONN_STR.
"""
import os
import logging
import asyncio
from config import Config

logger = logging.getLogger(__name__)

CONTAINER_NAME = "mmonitor-backup"
BLOB_NAME = "simulator.db"


def _get_client():
    conn_str = os.getenv("AZURE_STORAGE_CONN_STR")
    if not conn_str:
        return None
    from azure.storage.blob import BlobServiceClient
    return BlobServiceClient.from_connection_string(conn_str)


async def restore_db():
    """Download DB from blob on startup if local DB doesn't exist or is empty."""
    db_path = Config.DB_PATH
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        logger.info("Local DB exists, skipping restore")
        return False

    client = _get_client()
    if not client:
        logger.warning("No AZURE_STORAGE_CONN_STR, skipping DB restore")
        return False

    try:
        container = client.get_container_client(CONTAINER_NAME)
        blob = container.get_blob_client(BLOB_NAME)
        if not blob.exists():
            logger.info("No backup blob found, starting fresh")
            return False

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with open(db_path, "wb") as f:
            stream = blob.download_blob()
            f.write(stream.readall())
        logger.info(f"DB restored from blob ({os.path.getsize(db_path)} bytes)")
        return True
    except Exception as e:
        logger.error(f"DB restore failed: {e}")
        return False


async def backup_db():
    """Upload DB to blob storage."""
    db_path = Config.DB_PATH
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return False

    client = _get_client()
    if not client:
        return False

    try:
        container = client.get_container_client(CONTAINER_NAME)
        try:
            container.create_container()
        except Exception:
            pass  # already exists

        blob = container.get_blob_client(BLOB_NAME)
        with open(db_path, "rb") as f:
            blob.upload_blob(f, overwrite=True)
        logger.info(f"DB backed up to blob ({os.path.getsize(db_path)} bytes)")
        return True
    except Exception as e:
        logger.error(f"DB backup failed: {e}")
        return False