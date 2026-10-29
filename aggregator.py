# -*- coding: utf-8 -*-
#
# Combines per-instance stream DataFrames into one cross-instance view. `instance` is always kept
# in group keys below since the same triage store/component map name on different instances is a
# different real-world object.
#
import pandas as pd

import transform


def combineInstanceFrames(instance_dataframes):
    non_empty = [df for df in instance_dataframes if df is not None and not df.empty]
    if not non_empty:
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True)


def aggregateByColumnPerInstance(df, column):
    columns = ["instance", column, "stream_count", "distinct_project_count", "project_names"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    grouped = df.groupby(["instance", column]).agg(
        stream_count=("name", "count"),
        distinct_project_count=("primaryProjectName", "nunique"),
        project_names=("primaryProjectName", lambda values: ";".join(sorted({v for v in values if isinstance(v, str) and v}))),
    ).reset_index()
    return grouped.sort_values("stream_count", ascending=False)


#
# One row per (instance, project), including projects with zero streams, with per-project triage
# store/component map diversity merged in from the stream rows when available.
#
def aggregateProjectsPerInstance(df, projects_df):
    columns = ["instance", "primaryProjectName", "stream_count", "distinct_triage_store_count", "distinct_component_map_count", "projectOwner"]
    if projects_df is None or projects_df.empty:
        return pd.DataFrame(columns=columns)
    base = projects_df[["instance", "primaryProjectName", "stream_count", "projectOwner"]].copy()
    if df is not None and not df.empty:
        diversity = df.groupby(["instance", "primaryProjectName"]).agg(
            distinct_triage_store_count=("triageStoreName", "nunique"),
            distinct_component_map_count=("componentMapName", "nunique"),
        ).reset_index()
        base = base.merge(diversity, on=["instance", "primaryProjectName"], how="left")
    else:
        base["distinct_triage_store_count"] = 0
        base["distinct_component_map_count"] = 0
    base["distinct_triage_store_count"] = base["distinct_triage_store_count"].fillna(0).astype(int)
    base["distinct_component_map_count"] = base["distinct_component_map_count"].fillna(0).astype(int)
    return base[columns].sort_values("stream_count", ascending=False)


def streamsPerInstance(df):
    if df.empty:
        return pd.DataFrame(columns=["instance", "stream_count"])
    return df.groupby("instance").size().reset_index(name="stream_count").sort_values("stream_count", ascending=False)


#
# Finds values of name_column (e.g. stream name, project name) that occur in more than one
# instance, and reports whether their streams use the same triageStoreName/componentMapName
# everywhere - a *_consistent of False means the same name means different things per instance.
#
def aggregateCrossInstance(df, name_column):
    columns = [
        name_column, "instance_count", "instances", "stream_count",
        "triage_store_names", "triage_store_consistent",
        "component_map_names", "component_map_consistent",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)
    filtered = df[df[name_column].notna() & (df[name_column] != "")]
    if filtered.empty:
        return pd.DataFrame(columns=columns)
    grouped = filtered.groupby(name_column).agg(
        instance_count=("instance", "nunique"),
        instances=("instance", lambda values: ";".join(sorted({v for v in values if isinstance(v, str) and v}))),
        stream_count=("name", "count"),
        triage_store_names=("triageStoreName", lambda values: ";".join(sorted({v for v in values if isinstance(v, str) and v}))),
        component_map_names=("componentMapName", lambda values: ";".join(sorted({v for v in values if isinstance(v, str) and v}))),
    ).reset_index()
    grouped["triage_store_consistent"] = grouped["triage_store_names"].apply(lambda s: len(s.split(";")) <= 1)
    grouped["component_map_consistent"] = grouped["component_map_names"].apply(lambda s: len(s.split(";")) <= 1)
    grouped = grouped[grouped["instance_count"] > 1]
    return grouped.sort_values(["instance_count", "stream_count"], ascending=[False, False])


#
# Builds a one-row-per-instance table from the server diagnostics overview dicts, each already
# tagged with "instance" (see main.writeInstanceDiagnosticsJson).
#
def buildDiagnosticsByInstance(diagnostics_list):
    columns = [
        "instance", "numStreams", "numProjects", "numTriageStores", "numUsers", "numGroups",
        "numComponentMaps", "dbSize", "pendingCommits", "runningCommits", "commitGateOpen",
        "backUpInProgress", "skeletonizationInProgress", "numPendingEmails",
    ]
    if not diagnostics_list:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(diagnostics_list)
    for column in columns:
        if column not in df.columns:
            df[column] = None
    return df[columns]


def buildAggregatedReportData(instance_dataframes, failed_instances, diagnostics_list=None, project_dataframes=None, unused_triage_store_dataframes=None, unused_component_map_dataframes=None):
    combined = combineInstanceFrames(instance_dataframes)
    combined_projects = combineInstanceFrames(project_dataframes or [])
    combined_unused_triage_stores = combineInstanceFrames(unused_triage_store_dataframes or [])
    combined_unused_component_maps = combineInstanceFrames(unused_component_map_dataframes or [])
    cross_instance_projects = aggregateCrossInstance(combined, "primaryProjectName")
    cross_instance_streams = aggregateCrossInstance(combined, "name")
    diagnostics_by_instance = buildDiagnosticsByInstance(diagnostics_list or [])
    kpis = transform.computeKpis(combined, combined_projects)
    kpis["cross_instance_projects_count"] = len(cross_instance_projects)
    kpis["cross_instance_streams_count"] = len(cross_instance_streams)
    return {
        "streams": combined,
        "by_triage_store": aggregateByColumnPerInstance(combined, "triageStoreName"),
        "by_component_map": aggregateByColumnPerInstance(combined, "componentMapName"),
        "by_project": aggregateProjectsPerInstance(combined, combined_projects),
        "by_instance": streamsPerInstance(combined),
        "cross_instance_projects": cross_instance_projects,
        "cross_instance_streams": cross_instance_streams,
        "orphan_streams": transform.buildOrphanStreams(combined),
        "unused_triage_stores": combined_unused_triage_stores,
        "unused_component_maps": combined_unused_component_maps,
        "diagnostics_by_instance": diagnostics_by_instance,
        "kpis": kpis,
        "failed_instances": failed_instances,
    }
