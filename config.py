# -*- coding: utf-8 -*-
#
# Argument parsing, logging setup and multi-instance configuration loading for coverity_to_cnc.
#
import argparse
import json
import logging
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from tqdm import tqdm

__version__ = "0.2.0"


#
# Writes log records via tqdm.write() instead of directly to the stream, so they don't corrupt
# the carriage-return redraw of any active tqdm progress bar.
#
class TqdmLoggingHandler(logging.Handler):
    def emit(self, record):
        try:
            tqdm.write(self.format(record))
        except Exception:
            self.handleError(record)


@dataclass
class Instance:
    url: str
    username: str
    password: str
    verify_ssl: bool = True
    label: str = field(default="")

    def __post_init__(self):
        if not self.label:
            self.label = urlparse(self.url).netloc or self.url


def str2bool(value):
    if isinstance(value, bool):
        return value
    if value.lower() in ("yes", "true", "t", "y", "1"):
        return True
    if value.lower() in ("no", "false", "f", "n", "0"):
        return False
    raise argparse.ArgumentTypeError("Boolean value expected.")


def buildArgParser():
    parser = argparse.ArgumentParser(
        description="Report Coverity Connect streams, triage stores and component maps across one or more instances.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.register('type', 'bool', str2bool)
    parser.add_argument('--version', action='version', version=f'coverity_to_cnc {__version__}')
    parser.add_argument('--instances_file', help="Path to a JSON file listing multiple Coverity instances, see instances.example.json. Enables multi-instance mode. Each entry may set an optional 'label' to use as a nickname instead of the URL's host:port.", default=None)
    parser.add_argument('--aggregate_from_dir', help="Skip fetching; combine coverity_streams_data_*.json files (written next to each instance's report) from this directory into one aggregated cross-instance report. Use this when each instance can only be reached in a separate run.", default=None)
    parser.add_argument('--url', help="Coverity Connect URL for single-instance ad-hoc mode, e.g. https://coverity.example.com:443", default=None)
    parser.add_argument('--username', help="Username for single-instance mode. Falls back to COVERITY_USERNAME env var.", default=None)
    parser.add_argument('--password', help="Password for single-instance mode. Falls back to COVERITY_PASSWORD env var.", default=None)
    parser.add_argument('--verify_ssl', help="Verify SSL certificates in single-instance mode. default=True", type='bool', default=True)
    parser.add_argument('--output_dir', help="Directory where reports are written. default=current directory", default=".")
    parser.add_argument('--workers', help="Number of instances to fetch concurrently. default=1 (sequential)", type=int, default=1)
    parser.add_argument('--log_level', help="INFO or DEBUG. default=INFO", default="INFO")
    return parser


def setupLogging(log_level):
    handler = TqdmLoggingHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(module)s: %(message)s'))
    logging.basicConfig(level=log_level.upper(), handlers=[handler])
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def resolvePassword(explicit_password, env_var_name):
    if explicit_password:
        return explicit_password
    return os.environ.get(env_var_name)


#
# Loads instances either from --instances_file (multi-instance mode) or from --url/--username/--password
# (single-instance ad-hoc mode). Returns (instances, failed) where failed entries are config-level
# problems (e.g. missing password_env) that should be reported without aborting the whole run.
#
def loadInstances(args):
    instances = []
    failed = []
    if args.instances_file:
        with open(args.instances_file, "r") as f:
            data = json.load(f)
        for entry in data.get("instances", []):
            url = entry.get("url")
            label = entry.get("label") or (urlparse(url).netloc if url else None) or url or "unknown"
            username = entry.get("username")
            password_env = entry.get("password_env")
            password = os.environ.get(password_env) if password_env else None
            verify_ssl = entry.get("verify_ssl", True)
            if not url or not username or not password:
                failed.append({
                    "label": label,
                    "error": f"Missing url/username/password for instance entry (password_env={password_env})."
                })
                continue
            instances.append(Instance(url=url, username=username, password=password, verify_ssl=verify_ssl, label=label))
        return instances, failed

    if not args.url:
        raise SystemExit("Specify either --instances_file (multi-instance mode) or --url (single-instance mode).")
    username = args.username or os.environ.get('COVERITY_USERNAME') or ""
    password = resolvePassword(args.password, 'COVERITY_PASSWORD') or ""
    if not username or not password:
        raise SystemExit("Single-instance mode requires --username/--password (or COVERITY_USERNAME/COVERITY_PASSWORD env vars).")
    instances.append(Instance(url=args.url, username=username, password=password, verify_ssl=args.verify_ssl))
    return instances, failed
