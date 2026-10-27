# -*- coding: utf-8 -*-
#
# Parses raw Coverity stream/project payloads into pandas-ready rows, derived flags and aggregates.
#
import pandas as pd
from tqdm import tqdm

import api_client

STREAM_FIELDS = [
    "name", "primaryProjectName", "triageStoreName", "componentMapName", "language",
    "description", "outdated", "enableDesktopAnalysis", "ownerAssignmentOption",
    "summaryExpirationDays", "versionMismatchMessage", "analysisVersionOverride",
    "pluginVersionOverride",
]


#
# Collects distinct usernames from roleAssignments whose roleName contains role_name_contains
# (case-insensitive), e.g. "owner" matches "streamOwner" and "projectOwner".
#
def extractRoleUsernames(role_assignments, role_name_contains):
    if not role_assignments:
        return ""
    names = set()
    for role in role_assignments:
        role_name = role.get("roleName") or ""
        if role_name_contains.lower() in role_name.lower():
            username = role.get("username")
            if username:
                names.add(username)
    return ";".join(sorted(names))


def streamToRow(stream, instance_label):
    row = {field: stream.get(field) for field in STREAM_FIELDS}
    row["instance"] = instance_label
    row["streamOwner"] = extractRoleUsernames(stream.get("roleAssignments"), "owner")
    return row


def buildStreamRows(streams, instance_label):
    return [streamToRow(stream, instance_label) for stream in streams]


#
# Adds projectOwner/projectDescription to each row via GET /api/v2/projects/{name}, caching per
# project name so each distinct project is only fetched once per instance.
#
def enrichWithProjectDetails(rows, instance, skip_project_enrichment):
    if skip_project_enrichment:
        for row in rows:
            row["projectOwner"] = ""
            row["projectDescription"] = ""
        return rows

    project_cache = {}
    for row in tqdm(rows, desc=f"{instance.label}: enriching streams", unit="stream"):
        project_name = row.get("primaryProjectName")
        if not project_name:
            row["projectOwner"] = ""
            row["projectDescription"] = ""
            continue
        if project_name not in project_cache:
            project_cache[project_name] = api_client.getProjectDetails(instance, project_name)
        project = project_cache[project_name]
        if project:
            row["projectOwner"] = extractRoleUsernames(project.get("roleAssignments"), "owner")
            row["projectDescription"] = project.get("description") or ""
        else:
            row["projectOwner"] = ""
            row["projectDescription"] = ""
    return rows


def addDerivedFlags(df):
    df = df.copy()
    df["is_default_component_map"] = df["componentMapName"] == "Default"
    df["is_default_triage_store"] = df["triageStoreName"] == "Default Triage Store"
    df["missing_owner"] = df["streamOwner"].fillna("") == ""
    df["orphan_stream"] = df["primaryProjectName"].isna() | (df["primaryProjectName"] == "")
    return df


def buildStreamsDataFrame(rows):
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return addDerivedFlags(df)


def aggregateByColumn(df, column):
    columns = [column, "stream_count", "distinct_project_count", "project_names"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    grouped = df.groupby(column).agg(
        stream_count=("name", "count"),
        distinct_project_count=("primaryProjectName", "nunique"),
        project_names=("primaryProjectName", lambda values: ";".join(sorted({v for v in values if isinstance(v, str) and v}))),
    ).reset_index()
    return grouped.sort_values("stream_count", ascending=False)


def aggregateProjects(df):
    columns = ["primaryProjectName", "stream_count", "distinct_triage_store_count", "distinct_component_map_count", "projectOwner"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    grouped = df.groupby("primaryProjectName").agg(
        stream_count=("name", "count"),
        distinct_triage_store_count=("triageStoreName", "nunique"),
        distinct_component_map_count=("componentMapName", "nunique"),
        projectOwner=("projectOwner", lambda values: ";".join(sorted({v for v in values if isinstance(v, str) and v}))),
    ).reset_index()
    return grouped.sort_values("stream_count", ascending=False)


def buildAnomalies(df):
    if df.empty:
        return df
    outdated = df["outdated"].fillna(False).astype(bool)
    no_desktop_analysis = ~df["enableDesktopAnalysis"].fillna(True).astype(bool)
    return df[outdated | no_desktop_analysis | df["missing_owner"] | df["orphan_stream"]]


def computeKpis(df):
    total_streams = len(df)
    if total_streams == 0:
        return {
            "total_streams": 0, "total_projects": 0, "distinct_triage_stores": 0,
            "distinct_component_maps": 0, "pct_default_component_map": 0.0,
            "pct_default_triage_store": 0.0, "pct_outdated": 0.0, "missing_owner_count": 0,
        }
    return {
        "total_streams": total_streams,
        "total_projects": int(df["primaryProjectName"].nunique()),
        "distinct_triage_stores": int(df["triageStoreName"].nunique()),
        "distinct_component_maps": int(df["componentMapName"].nunique()),
        "pct_default_component_map": round(100.0 * df["is_default_component_map"].sum() / total_streams, 1),
        "pct_default_triage_store": round(100.0 * df["is_default_triage_store"].sum() / total_streams, 1),
        "pct_outdated": round(100.0 * df["outdated"].fillna(False).astype(bool).sum() / total_streams, 1),
        "missing_owner_count": int(df["missing_owner"].sum()),
    }


#
# Bundles the DataFrames + KPIs shared by both the CSV and HTML writers for one instance.
#
def buildInstanceReportData(df):
    return {
        "streams": df,
        "by_triage_store": aggregateByColumn(df, "triageStoreName"),
        "by_component_map": aggregateByColumn(df, "componentMapName"),
        "by_project": aggregateProjects(df),
        "anomalies": buildAnomalies(df),
        "kpis": computeKpis(df),
        "failed_instances": [],
    }
