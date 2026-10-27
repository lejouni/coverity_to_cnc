# -*- coding: utf-8 -*-
#
# Coverity Connect REST API calls: GET /api/v2/streams and GET /api/v2/projects/{name}.
#
import json
import logging

import requests

HEADERS = {'Accept': 'application/json'}


class InstanceError(Exception):
    """Raised when a call fails badly enough that the whole instance should be skipped."""


#
# Returns the raw "streams" list for the given instance. If streams_file is given, reads it
# from disk instead of calling the live API (offline mode, single-instance only).
#
def getAllStreams(instance, streams_file=None):
    if streams_file:
        logging.debug(f"Loading streams from local file: {streams_file} (instance={instance.label})")
        with open(streams_file, "r") as f:
            data = json.load(f)
        return data.get("streams", [])

    endpoint = f"{instance.url}/api/v2/streams"
    logging.debug(f"Calling Coverity URL: {endpoint} (instance={instance.label})")
    try:
        r = requests.get(endpoint, headers=HEADERS, auth=(instance.username, instance.password), verify=instance.verify_ssl)
    except requests.exceptions.RequestException as e:
        raise InstanceError(f"Request to {endpoint} failed: {e}")
    if r.status_code != 200:
        raise InstanceError(f"GET {endpoint} returned status {r.status_code}: {r.text}")
    return r.json().get("streams", [])


#
# Returns a single project's detail dict, or None on any failure (non-fatal - caller continues).
#
def getProjectDetails(instance, project_name):
    endpoint = f"{instance.url}/api/v2/projects/{project_name}?includeChildren=false&includeStreams=false&locale=en_us"
    logging.debug(f"Calling Coverity URL: {endpoint} (instance={instance.label})")
    try:
        r = requests.get(endpoint, headers=HEADERS, auth=(instance.username, instance.password), verify=instance.verify_ssl)
    except requests.exceptions.RequestException as e:
        logging.warning(f"Request for project '{project_name}' on instance {instance.label} failed: {e}")
        return None
    if r.status_code != 200:
        logging.warning(f"GET {endpoint} returned status {r.status_code} for project '{project_name}' on instance {instance.label}: {r.text}")
        return None
    projects = r.json().get("projects", [])
    if not projects:
        logging.warning(f"No project data found for project '{project_name}' on instance {instance.label}")
        return None
    return projects[0]
