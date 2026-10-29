# -*- coding: utf-8 -*-
#
# Parses the raw Coverity /api/v2/projects (with nested streams) payload into pandas-ready rows,
# derived flags and aggregates.
#
import pandas as pd
from tqdm import tqdm

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


def streamToRow(stream, project, instance_label):
    row = {field: stream.get(field) for field in STREAM_FIELDS}
    if not row.get("primaryProjectName"):
        row["primaryProjectName"] = project.get("name")
    row["instance"] = instance_label
    row["streamOwner"] = extractRoleUsernames(stream.get("roleAssignments"), "owner")
    row["projectOwner"] = extractRoleUsernames(project.get("roleAssignments"), "owner")
    row["projectDescription"] = project.get("description") or ""
    return row


#
# Flattens the nested projects/streams payload into one row per stream, carrying each stream's
# parent project owner/description along - no extra per-project API calls needed.
#
def buildStreamRows(projects, instance_label):
    rows = []
    for project in tqdm(projects, desc=f"{instance_label}: building stream rows", unit="project"):
        for stream in project.get("streams", []):
            rows.append(streamToRow(stream, project, instance_label))
    return rows


#
# Builds rows for the streams listed under the built-in "Other Streams" pseudo-project (id -1) in
# GET {url}/config/projects.json - i.e. streams with no parent project. primaryProjectName/
# projectOwner/projectDescription are left blank since there's no parent project to pull them
# from; addDerivedFlags already flags these via orphan_stream.
#
def buildOrphanStreamRows(streams, instance_label):
    rows = []
    for stream in streams:
        row = {field: stream.get(field) for field in STREAM_FIELDS}
        row["primaryProjectName"] = row.get("primaryProjectName") or ""
        row["instance"] = instance_label
        row["streamOwner"] = extractRoleUsernames(stream.get("roleAssignments"), "owner")
        row["projectOwner"] = ""
        row["projectDescription"] = ""
        rows.append(row)
    return rows


#
# One row per top-level triage store whose "children" list is empty, i.e. not attached to any
# project/stream and therefore unused (per GET {url}/config/triagestores.json).
#
def buildUnusedTriageStoreRows(triage_stores, instance_label):
    rows = []
    for store in triage_stores or []:
        if len(store.get("children") or []) == 0:
            rows.append({
                "triageStoreId": store.get("id"),
                "triageStoreName": store.get("name"),
                "description": store.get("description") or "",
                "builtIn": store.get("builtIn", False),
                "instance": instance_label,
            })
    return rows


def buildUnusedTriageStoresDataFrame(rows):
    return pd.DataFrame(rows)


#
# One row per top-level component map whose own "streams" list is empty, i.e. not attached to any
# stream and therefore unused (per GET {url}/config/componentmaps.json). Unlike triage stores,
# "children" here are the map's component/folder definitions, not stream attachments, so "streams"
# is the field that signals usage.
#
def buildUnusedComponentMapRows(component_maps, instance_label):
    rows = []
    for component_map in component_maps or []:
        if len(component_map.get("streams") or []) == 0:
            rows.append({
                "componentMapId": component_map.get("id"),
                "componentMapName": component_map.get("name"),
                "description": component_map.get("description") or "",
                "builtIn": component_map.get("builtIn", False),
                "instance": instance_label,
            })
    return rows


def buildUnusedComponentMapsDataFrame(rows):
    return pd.DataFrame(rows)


#
# One row per project (including projects with zero streams), so project counts/listings don't
# silently drop streamless projects the way grouping the stream rows would.
#
def projectToRow(project, instance_label):
    return {
        "primaryProjectName": project.get("name"),
        "projectDescription": project.get("description") or "",
        "projectOwner": extractRoleUsernames(project.get("roleAssignments"), "owner"),
        "instance": instance_label,
        "stream_count": len(project.get("streams", [])),
    }


def buildProjectRows(projects, instance_label):
    return [projectToRow(project, instance_label) for project in projects]


def buildProjectsDataFrame(rows):
    return pd.DataFrame(rows)


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


#
# One row per project with its stream_count and projectOwner (from the full project list, so
# streamless projects are included), plus per-project triage store/component map diversity
# (from the stream rows, when available).
#
def aggregateProjects(df, projects_df):
    columns = ["primaryProjectName", "stream_count", "distinct_triage_store_count", "distinct_component_map_count", "projectOwner"]
    if projects_df is None or projects_df.empty:
        return pd.DataFrame(columns=columns)
    base = projects_df[["primaryProjectName", "stream_count", "projectOwner"]].copy()
    if df is not None and not df.empty:
        diversity = df.groupby("primaryProjectName").agg(
            distinct_triage_store_count=("triageStoreName", "nunique"),
            distinct_component_map_count=("componentMapName", "nunique"),
        ).reset_index()
        base = base.merge(diversity, on="primaryProjectName", how="left")
    else:
        base["distinct_triage_store_count"] = 0
        base["distinct_component_map_count"] = 0
    base["distinct_triage_store_count"] = base["distinct_triage_store_count"].fillna(0).astype(int)
    base["distinct_component_map_count"] = base["distinct_component_map_count"].fillna(0).astype(int)
    return base[columns].sort_values("stream_count", ascending=False)


def buildOrphanStreams(df):
    if df.empty:
        return df
    return df[df["orphan_stream"]]


def computeKpis(df, projects_df=None):
    total_streams = len(df)
    total_projects = int(projects_df["primaryProjectName"].nunique()) if projects_df is not None and not projects_df.empty else 0
    if total_streams == 0:
        return {
            "total_streams": 0, "total_projects": total_projects, "distinct_triage_stores": 0,
            "distinct_component_maps": 0, "pct_default_component_map": 0.0,
            "pct_default_triage_store": 0.0, "pct_outdated": 0.0, "missing_owner_count": 0,
        }
    return {
        "total_streams": total_streams,
        "total_projects": total_projects,
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
def buildInstanceReportData(df, projects_df=None, diagnostics=None, unused_triage_stores_df=None, unused_component_maps_df=None):
    return {
        "streams": df,
        "by_triage_store": aggregateByColumn(df, "triageStoreName"),
        "by_component_map": aggregateByColumn(df, "componentMapName"),
        "by_project": aggregateProjects(df, projects_df),
        "orphan_streams": buildOrphanStreams(df),
        "unused_triage_stores": unused_triage_stores_df if unused_triage_stores_df is not None else pd.DataFrame(),
        "unused_component_maps": unused_component_maps_df if unused_component_maps_df is not None else pd.DataFrame(),
        "kpis": computeKpis(df, projects_df),
        "diagnostics": diagnostics or {},
        "failed_instances": [],
    }
