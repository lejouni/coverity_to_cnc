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


def aggregateProjectsPerInstance(df):
    columns = ["instance", "primaryProjectName", "stream_count", "distinct_triage_store_count", "distinct_component_map_count", "projectOwner"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    grouped = df.groupby(["instance", "primaryProjectName"]).agg(
        stream_count=("name", "count"),
        distinct_triage_store_count=("triageStoreName", "nunique"),
        distinct_component_map_count=("componentMapName", "nunique"),
        projectOwner=("projectOwner", lambda values: ";".join(sorted({v for v in values if isinstance(v, str) and v}))),
    ).reset_index()
    return grouped.sort_values("stream_count", ascending=False)


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


def buildAggregatedReportData(instance_dataframes, failed_instances):
    combined = combineInstanceFrames(instance_dataframes)
    cross_instance_projects = aggregateCrossInstance(combined, "primaryProjectName")
    cross_instance_streams = aggregateCrossInstance(combined, "name")
    kpis = transform.computeKpis(combined)
    kpis["cross_instance_projects_count"] = len(cross_instance_projects)
    kpis["cross_instance_streams_count"] = len(cross_instance_streams)
    return {
        "streams": combined,
        "by_triage_store": aggregateByColumnPerInstance(combined, "triageStoreName"),
        "by_component_map": aggregateByColumnPerInstance(combined, "componentMapName"),
        "by_project": aggregateProjectsPerInstance(combined),
        "by_instance": streamsPerInstance(combined),
        "cross_instance_projects": cross_instance_projects,
        "cross_instance_streams": cross_instance_streams,
        "anomalies": transform.buildAnomalies(combined),
        "kpis": kpis,
        "failed_instances": failed_instances,
    }
