# -*- coding: utf-8 -*-
#
# Entry point: fetch Coverity streams/projects from one or more instances and write CSV/HTML reports.
#
import glob
import logging
import os
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
# Runs Phase 1-3 for a single instance. Returns (label, dataframe, error) - error is None on success.
#
def processInstance(instance, args):
    try:
        streams = api_client.getAllStreams(instance, streams_file=args.streams_file)
        rows = transform.buildStreamRows(streams, instance.label)
        rows = transform.enrichWithProjectDetails(rows, instance, args.skip_project_enrichment)
        df = transform.buildStreamsDataFrame(rows)
        logging.info(f"Instance {instance.label}: collected {len(df)} streams.")
        return instance.label, df, None
    except api_client.InstanceError as e:
        logging.error(f"Instance {instance.label}: {e}")
        return instance.label, None, str(e)
    except Exception as e:
        # A single bad instance must not abort the whole multi-instance run.
        logging.exception(f"Instance {instance.label}: unexpected error while processing.")
        return instance.label, None, str(e)


def writeInstanceReports(label, df, output_dir, timestamp):
    report_data = transform.buildInstanceReportData(df)
    safe_label = label.replace(":", "_").replace("/", "_")
    report_csv.writeCsv(report_data, output_dir, f"coverity_streams_report_{safe_label}_{timestamp}.csv")
    report_html.writeHtml(
        f"Coverity Streams Report - {label}", report_data, output_dir,
        f"coverity_streams_report_{safe_label}_{timestamp}.html"
    )
    writeInstanceDataJson(df, output_dir, f"coverity_streams_data_{safe_label}_{timestamp}.json")


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
    aggregated_data = aggregator.buildAggregatedReportData(instance_dataframes, [])
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
                label, df, error = future.result()
                if error:
                    failed_instances.append({"label": label, "error": error})
                else:
                    writeInstanceReports(label, df, args.output_dir, timestamp)
    else:
        for instance in tqdm(instances, desc="Instances", unit="instance"):
            label, df, error = processInstance(instance, args)
            if error:
                failed_instances.append({"label": label, "error": error})
            else:
                writeInstanceReports(label, df, args.output_dir, timestamp)

    # Aggregated cross-instance reports are only built via --aggregate_from_dir, not here, so
    # that it works the same whether instances were fetched together or in separate runs.
    if failed_instances:
        logging.warning(f"Failed instances ({len(failed_instances)}): {failed_instances}")

    logging.info("Done.")


if __name__ == '__main__':
    main()
