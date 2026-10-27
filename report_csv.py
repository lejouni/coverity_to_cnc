# -*- coding: utf-8 -*-
#
# Writes the flat per-stream report to CSV.
#
import logging
import os


def writeCsv(report_data, output_dir, filename):
    path = os.path.join(output_dir, filename)
    report_data["streams"].to_csv(path, index=False)
    logging.info(f"Wrote CSV report: {path}")
    return path
