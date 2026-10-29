# -*- coding: utf-8 -*-
#
# Entry point: fetch Coverity streams/projects from one or more instances and write CSV/HTML reports.
#
import glob
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
from tqdm import tqdm

import aggregator
import api_client
import config
import report_csv
import report_html
import transform


#
# Stamps report_data with the script version and a human-readable generation timestamp, read by
# report_html/report_csv so every report shows what produced it and when.
#
def addReportMetadata(report_data):
    report_data["version"] = config.__version__
    report_data["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return report_data


#
# Runs Phase 1-3 for a single instance, plus best-effort diagnostics overview, triage store and
# component map fetches. Returns (label, dataframe, projects_dataframe, diagnostics,
# unused_triage_stores_dataframe, unused_component_maps_dataframe, error) - error is None on
# success, diagnostics is {} if that call failed (non-fatal).
#
def processInstance(instance, args):
    try:
        projects = api_client.getAllProjects(instance, projects_file=args.projects_file)
        rows = transform.buildStreamRows(projects, instance.label)
        if not args.projects_file:
            orphan_streams = api_client.getOrphanStreams(instance)
            if orphan_streams is not None:
                orphan_rows = transform.buildOrphanStreamRows(orphan_streams, instance.label)
                if orphan_rows:
                    logging.warning(f"Instance {instance.label}: {len(orphan_rows)} stream(s) have no parent project: {[row['name'] for row in orphan_rows]}")
                rows += orphan_rows
        df = transform.buildStreamsDataFrame(rows)
        projects_df = transform.buildProjectsDataFrame(transform.buildProjectRows(projects, instance.label))
        logging.info(f"Instance {instance.label}: collected {len(df)} streams across {len(projects_df)} projects.")
        diagnostics = api_client.getDiagnosticsOverview(instance) or {}
        unused_triage_stores_df = transform.buildUnusedTriageStoresDataFrame([])
        unused_component_maps_df = transform.buildUnusedComponentMapsDataFrame([])
        if not args.projects_file:
            triage_stores = api_client.getAllTriageStores(instance)
            if triage_stores is not None:
                unused_triage_store_rows = transform.buildUnusedTriageStoreRows(triage_stores, instance.label)
                if unused_triage_store_rows:
                    logging.warning(f"Instance {instance.label}: {len(unused_triage_store_rows)} triage store(s) have no associated streams: {[row['triageStoreName'] for row in unused_triage_store_rows]}")
                unused_triage_stores_df = transform.buildUnusedTriageStoresDataFrame(unused_triage_store_rows)
            component_maps = api_client.getAllComponentMaps(instance)
            if component_maps is not None:
                unused_component_map_rows = transform.buildUnusedComponentMapRows(component_maps, instance.label)
                if unused_component_map_rows:
                    logging.warning(f"Instance {instance.label}: {len(unused_component_map_rows)} component map(s) have no associated streams: {[row['componentMapName'] for row in unused_component_map_rows]}")
                unused_component_maps_df = transform.buildUnusedComponentMapsDataFrame(unused_component_map_rows)
        return instance.label, df, projects_df, diagnostics, unused_triage_stores_df, unused_component_maps_df, None
    except api_client.InstanceError as e:
        logging.error(f"Instance {instance.label}: {e}")
        return instance.label, None, None, {}, None, None, str(e)
    except Exception as e:
        # A single bad instance must not abort the whole multi-instance run.
        logging.exception(f"Instance {instance.label}: unexpected error while processing.")
        return instance.label, None, None, {}, None, None, str(e)


def writeInstanceReports(label, df, projects_df, diagnostics, unused_triage_stores_df, unused_component_maps_df, output_dir, timestamp):
    report_data = transform.buildInstanceReportData(df, projects_df, diagnostics, unused_triage_stores_df, unused_component_maps_df)
    addReportMetadata(report_data)
    safe_label = label.replace(":", "_").replace("/", "_")
    report_csv.writeCsv(report_data, output_dir, f"coverity_streams_report_{safe_label}_{timestamp}.csv")
    report_html.writeHtml(
        f"Coverity Streams Report - {label}", report_data, output_dir,
        f"coverity_streams_report_{safe_label}_{timestamp}.html"
    )
    writeInstanceDataJson(df, output_dir, f"coverity_streams_data_{safe_label}_{timestamp}.json")
    writeInstanceDataJson(projects_df, output_dir, f"coverity_projects_data_{safe_label}_{timestamp}.json")
    writeInstanceDataJson(unused_triage_stores_df, output_dir, f"coverity_triage_stores_data_{safe_label}_{timestamp}.json")
    writeInstanceDataJson(unused_component_maps_df, output_dir, f"coverity_component_maps_data_{safe_label}_{timestamp}.json")
    writeInstanceDiagnosticsJson(label, diagnostics, output_dir, f"coverity_diagnostics_{safe_label}_{timestamp}.json")


#
# Saves the fully enriched per-instance streams DataFrame so it can be combined later by
# --aggregate_from_dir, e.g. when each instance can only be reached in a separate run/network.
#
def writeInstanceDataJson(df, output_dir, filename):
    path = os.path.join(output_dir, filename)
    df.to_json(path, orient="records")
    logging.info(f"Wrote data file for later aggregation: {path}")
    return path


#
# Saves one instance's diagnostics overview (tagged with its label) so --aggregate_from_dir can
# rebuild the per-instance diagnostics table without contacting any Coverity instance.
#
def writeInstanceDiagnosticsJson(label, diagnostics, output_dir, filename):
    path = os.path.join(output_dir, filename)
    with open(path, "w") as f:
        json.dump({"instance": label, **diagnostics}, f)
    logging.info(f"Wrote diagnostics file for later aggregation: {path}")
    return path


#
# Combines previously saved coverity_streams_data_*.json files from data_dir into one aggregated
# cross-instance report, without contacting any Coverity instance.
#
def buildAggregateOnlyReport(data_dir, output_dir, timestamp):
    data_files = sorted(glob.glob(os.path.join(data_dir, "coverity_streams_data_*.json")))
    if not data_files:
        logging.error(f"No coverity_streams_data_*.json files found in {data_dir}.")
        sys.exit(1)
    instance_dataframes = []
    for path in data_files:
        df = pd.read_json(path, orient="records")
        logging.info(f"Loaded {len(df)} streams from {path}.")
        instance_dataframes.append(df)
    project_dataframes = []
    for path in sorted(glob.glob(os.path.join(data_dir, "coverity_projects_data_*.json"))):
        df = pd.read_json(path, orient="records")
        logging.info(f"Loaded {len(df)} projects from {path}.")
        project_dataframes.append(df)
    unused_triage_store_dataframes = []
    for path in sorted(glob.glob(os.path.join(data_dir, "coverity_triage_stores_data_*.json"))):
        df = pd.read_json(path, orient="records")
        logging.info(f"Loaded {len(df)} unused triage store(s) from {path}.")
        unused_triage_store_dataframes.append(df)
    unused_component_map_dataframes = []
    for path in sorted(glob.glob(os.path.join(data_dir, "coverity_component_maps_data_*.json"))):
        df = pd.read_json(path, orient="records")
        logging.info(f"Loaded {len(df)} unused component map(s) from {path}.")
        unused_component_map_dataframes.append(df)
    diagnostics_list = []
    instance_reports = {}
    for path in sorted(glob.glob(os.path.join(data_dir, "coverity_diagnostics_*.json"))):
        with open(path, "r") as f:
            diagnostics = json.load(f)
        diagnostics_list.append(diagnostics)
        # coverity_diagnostics_<label>_<timestamp>.json and coverity_streams_report_<label>_<timestamp>.html
        # share the same "<label>_<timestamp>" suffix, written together by writeInstanceReports.
        match = re.match(r"^coverity_diagnostics_(.+)\.json$", os.path.basename(path))
        if match:
            html_path = os.path.join(data_dir, f"coverity_streams_report_{match.group(1)}.html")
            if os.path.exists(html_path):
                instance_reports[diagnostics.get("instance", match.group(1))] = os.path.relpath(html_path, output_dir)
    aggregated_data = aggregator.buildAggregatedReportData(instance_dataframes, [], diagnostics_list, project_dataframes, unused_triage_store_dataframes, unused_component_map_dataframes)
    aggregated_data["instance_reports"] = sorted(instance_reports.items())
    addReportMetadata(aggregated_data)
    report_csv.writeCsv(aggregated_data, output_dir, f"coverity_streams_report_ALL_{timestamp}.csv")
    report_html.writeHtml(
        "Coverity Streams Report - All Instances", aggregated_data, output_dir,
        f"coverity_streams_report_ALL_{timestamp}.html", is_aggregated=True
    )
    logging.info(f"Aggregated KPIs: {aggregated_data['kpis']}")


def main():
    parser = config.buildArgParser()
    args = parser.parse_args()
    config.setupLogging(args.log_level)
    logging.info(f"coverity_to_cnc version: {config.__version__}")

    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.aggregate_from_dir:
        buildAggregateOnlyReport(args.aggregate_from_dir, args.output_dir, timestamp)
        logging.info("Done.")
        return

    instances, failed_instances = config.loadInstances(args)
    if not instances:
        logging.error("No usable Coverity instances to process.")
        for failed in failed_instances:
            logging.error(f"Instance {failed['label']}: {failed['error']}")
        sys.exit(1)

    worker_count = min(args.workers, len(instances))
    if worker_count > 1:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(processInstance, instance, args) for instance in instances]
            for future in tqdm(as_completed(futures), total=len(futures), desc="Instances", unit="instance"):
                label, df, projects_df, diagnostics, unused_triage_stores_df, unused_component_maps_df, error = future.result()
                if error:
                    failed_instances.append({"label": label, "error": error})
                else:
                    writeInstanceReports(label, df, projects_df, diagnostics, unused_triage_stores_df, unused_component_maps_df, args.output_dir, timestamp)
    else:
        for instance in tqdm(instances, desc="Instances", unit="instance"):
            label, df, projects_df, diagnostics, unused_triage_stores_df, unused_component_maps_df, error = processInstance(instance, args)
            if error:
                failed_instances.append({"label": label, "error": error})
            else:
                writeInstanceReports(label, df, projects_df, diagnostics, unused_triage_stores_df, unused_component_maps_df, args.output_dir, timestamp)

    # Aggregated cross-instance reports are only built via --aggregate_from_dir, not here, so
    # that it works the same whether instances were fetched together or in separate runs.
    if failed_instances:
        logging.warning(f"Failed instances ({len(failed_instances)}): {failed_instances}")

    logging.info("Done.")


if __name__ == '__main__':
    main()
