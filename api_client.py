# -*- coding: utf-8 -*-
#
# Coverity Connect REST API calls: GET /api/v2/projects (with nested streams) and the diagnostics
# overview endpoint.
#
import json
import logging

import requests

HEADERS = {'Accept': 'application/json'}


class InstanceError(Exception):
    """Raised when a call fails badly enough that the whole instance should be skipped."""


#
# Returns the raw "projects" list (each with a nested "streams" list, including projects with no
# streams) for the given instance. If projects_file is given, reads it from disk instead of
# calling the live API (offline mode, single-instance only). One call replaces the old
# /api/v2/streams + per-project detail lookups.
#
# Pages through the results via pageSize/offset: the "Retrieve all projects" docs don't list these
# params, but some Coverity Connect versions silently cap a single response's row count, which can
# drop projects the caller otherwise has access to. Requesting a large pageSize and following
# offset is harmless if the server ignores both (the first page already has everything, so the
# loop exits after one call) and fixes the drop if the server does honor them.
#
def getAllProjects(instance, projects_file=None):
    if projects_file:
        logging.debug(f"Loading projects from local file: {projects_file} (instance={instance.label})")
        with open(projects_file, "r") as f:
            data = json.load(f)
        return data.get("projects", [])

    projects = []
    seen_keys = set()
    page_size = 1000
    offset = 0
    max_pages = 50
    for page_num in range(max_pages):
        endpoint = f"{instance.url}/api/v2/projects?includeChildren=true&includeStreams=true&locale=en_us&pageSize={page_size}&offset={offset}"
        logging.debug(f"Calling Coverity URL: {endpoint} (instance={instance.label})")
        try:
            r = requests.get(endpoint, headers=HEADERS, auth=(instance.username, instance.password), verify=instance.verify_ssl)
        except requests.exceptions.RequestException as e:
            raise InstanceError(f"Request to {endpoint} failed: {e}")
        if r.status_code != 200:
            raise InstanceError(f"GET {endpoint} returned status {r.status_code}: {r.text}")
        page = r.json().get("projects", [])
        new_count = 0
        for project in page:
            key = project.get("projectKey", project.get("name"))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            projects.append(project)
            new_count += 1
        logging.debug(f"Instance {instance.label}: page {page_num} (offset={offset}) returned {len(page)} project(s), {new_count} new.")
        # Don't stop just because this page is smaller than page_size - the server may silently
        # cap each response below what we requested, which is the very truncation we're guarding
        # against. Only stop on a genuinely empty page or once offset/no new projects stalls.
        if not page or new_count == 0:
            break
        offset += len(page)
    else:
        logging.warning(f"Instance {instance.label}: stopped paging /api/v2/projects after {max_pages} pages - results may be incomplete.")
    return projects


#
# Returns the raw "projects" list from GET {url}/config/projects.json, or None on any failure
# (non-fatal). Each real project's "children" are its own streams (len(children)==0 means the
# project truly has zero streams - the authoritative signal, since /api/v2/projects can silently
# drop projects/streams on some server versions, see paging note above). The built-in "Other
# Streams" pseudo-project (id -1) groups streams with no parent project instead.
#
def getProjectsConfig(instance):
    endpoint = f"{instance.url}/config/projects.json"
    logging.debug(f"Calling Coverity URL: {endpoint} (instance={instance.label})")
    try:
        r = requests.get(endpoint, headers=HEADERS, auth=(instance.username, instance.password), verify=instance.verify_ssl)
    except requests.exceptions.RequestException as e:
        logging.warning(f"Request for projects config on instance {instance.label} failed: {e}")
        return None
    if r.status_code != 200:
        logging.warning(f"GET {endpoint} returned status {r.status_code} for projects config on instance {instance.label}: {r.text}")
        return None
    return r.json().get("projects", [])


#
# Returns the orphan streams (no parent project) from an already-fetched getProjectsConfig() list
# - the built-in "Other Streams" pseudo-project (id -1) groups them under "children".
#
def getOrphanStreams(projects_config):
    for project in projects_config or []:
        if project.get("id") == -1:
            return project.get("children", [])
    return []


#
# Returns the raw, nested "triageStores" list for the given instance (each entry may have a
# "children" list of the triage store configs actually attached to a project/stream), or None on
# any failure (non-fatal). A top-level entry with an empty "children" list is not attached to any
# project/stream, so it's reported as unused.
#
def getAllTriageStores(instance):
    endpoint = f"{instance.url}/config/triagestores.json"
    logging.debug(f"Calling Coverity URL: {endpoint} (instance={instance.label})")
    try:
        r = requests.get(endpoint, headers=HEADERS, auth=(instance.username, instance.password), verify=instance.verify_ssl)
    except requests.exceptions.RequestException as e:
        logging.warning(f"Request for triage stores on instance {instance.label} failed: {e}")
        return None
    if r.status_code != 200:
        logging.warning(f"GET {endpoint} returned status {r.status_code} for triage stores on instance {instance.label}: {r.text}")
        return None
    return r.json().get("triageStores", [])


#
# Returns the raw, nested "componentmaps" list for the given instance, or None on any failure
# (non-fatal). Each entry's own "streams" list is the signal to use for "is this attached to any
# stream" - unlike triage stores, a component map's "children" are its component/folder
# definitions (e.g. the built-in "Other"), which are never empty, so they can't indicate usage.
#
def getAllComponentMaps(instance):
    endpoint = f"{instance.url}/config/componentmaps.json"
    logging.debug(f"Calling Coverity URL: {endpoint} (instance={instance.label})")
    try:
        r = requests.get(endpoint, headers=HEADERS, auth=(instance.username, instance.password), verify=instance.verify_ssl)
    except requests.exceptions.RequestException as e:
        logging.warning(f"Request for component maps on instance {instance.label} failed: {e}")
        return None
    if r.status_code != 200:
        logging.warning(f"GET {endpoint} returned status {r.status_code} for component maps on instance {instance.label}: {r.text}")
        return None
    return r.json().get("componentmaps", [])


#
# Returns the server diagnostics overview dict (commit queue, counts, db size, ...), or None on
# any failure (non-fatal - caller continues without these extra metrics).
#
def getDiagnosticsOverview(instance):
    endpoint = f"{instance.url}/diagnostics/overview.json"
    logging.debug(f"Calling Coverity URL: {endpoint} (instance={instance.label})")
    try:
        r = requests.get(endpoint, headers=HEADERS, auth=(instance.username, instance.password), verify=instance.verify_ssl)
    except requests.exceptions.RequestException as e:
        logging.warning(f"Request for diagnostics overview on instance {instance.label} failed: {e}")
        return None
    if r.status_code != 200:
        logging.warning(f"GET {endpoint} returned status {r.status_code} for diagnostics overview on instance {instance.label}: {r.text}")
        return None
    return r.json()
