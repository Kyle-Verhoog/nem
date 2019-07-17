import logging
import sys

import colouredlogs


def get_logger(name):
    logger = logging.getLogger(name)
    colouredlogs.install(
        logger=logger,
        level=logging.WARN,
        stream=sys.stdout,
        datefmt='%H:%M:%S',
    )
    return logger
