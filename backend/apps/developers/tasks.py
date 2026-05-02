import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def refresh_jar_data() -> dict:
    """Monthly task to re-import JAR data.

    In the future this will download the latest dump from the open data portal.
    For now it logs a reminder that the import should be run manually.
    """
    # TODO: Download latest JAR dump from https://www.registrucentras.lt/atviri-duomenys/
    # and call: call_command("import_jar_dump", "/path/to/downloaded/file.csv")
    # For now, this task serves as a placeholder for scheduled execution.
    # Manual usage: python manage.py import_jar_dump /path/to/jar_data.csv
    logger.info("refresh_jar_data triggered — manual import required for now")

    return {
        "status": "skipped",
        "message": "Automatic download not yet implemented. Run import_jar_dump manually.",
    }
