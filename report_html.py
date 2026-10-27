# -*- coding: utf-8 -*-
#
# Builds a single self-contained interactive HTML report: KPI cards, Chart.js usage charts and
# DataTables-powered sortable/searchable tables. All JS/CSS is loaded from CDN - no new pip
# dependency is needed for the charts/tables themselves.
#
import html
import json
import logging
import os

DATATABLES_CSS = "https://cdn.datatables.net/1.13.8/css/jquery.dataTables.min.css"
JQUERY_JS = "https://code.jquery.com/jquery-3.7.1.min.js"
DATATABLES_JS = "https://cdn.datatables.net/1.13.8/js/jquery.dataTables.min.js"
CHARTJS_JS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"

TABLE_DESCRIPTIONS = {
    "streamsTable": "One row per stream collected from this instance, with its primary project, triage store, component map, owner and configuration flags.",
    "projectsTable": "One row per project: how many streams it owns, how many distinct triage stores/component maps those streams use, and the project owner.",
    "triageStoreTable": "One row per triage store: how many streams and distinct projects use it. Helps spot triage stores that are over- or under-used.",
    "componentMapTable": "One row per component map: how many streams and distinct projects use it.",
    "anomaliesTable": "Streams flagged for review: outdated, desktop analysis disabled, missing owner, or orphaned (no primary project).",
    "failedInstancesTable": "Instances that could not be processed, e.g. due to connection, authentication or API errors.",
    "crossInstanceProjectsTable": "Project names that exist in more than one instance, with the instances they appear in and whether every instance uses the same triage store/component map name for that project's streams. 'No' under *_consistent flags a naming collision worth reviewing before consolidating into one CNC instance.",
    "crossInstanceStreamsTable": "Stream names that exist in more than one instance, with the instances they appear in and whether every instance uses the same triage store/component map name for that stream. 'No' under *_consistent flags a naming collision worth reviewing before consolidating into one CNC instance.",
}


#
# Renders a small "i" badge whose native title tooltip explains what the section shows.
#
def _infoIcon(description):
    if not description:
        return ""
    return f'<span class="info-icon" title="{html.escape(description)}">i</span>'


def _heading(level, text, table_id):
    return f"<{level}>{text} {_infoIcon(TABLE_DESCRIPTIONS.get(table_id, ''))}</{level}>"


def _tableHtml(df, table_id):
    if df is None or df.empty:
        return "<p><em>No data.</em></p>"
    return df.fillna("").to_html(classes="display", table_id=table_id, index=False, border=0, escape=True)


#
# Turns a ";"-joined value (project names, instances, ...) into a readable comma list. Cells wrap
# via CSS rather than truncating, so the full set of names is always visible.
#
def _formatNameListCell(value):
    names = [n for n in str(value).split(";") if n]
    if not names:
        return ""
    return ", ".join(html.escape(n) for n in names)


#
# Renders a Yes/No consistency flag as a colored badge instead of a bare Python bool.
#
def _formatBoolCell(value):
    is_yes = bool(value)
    css_class = "badge-yes" if is_yes else "badge-no"
    return f'<span class="{css_class}">{"Yes" if is_yes else "No"}</span>'


#
# Like _tableHtml but renders selected columns as escaped/formatted HTML directly, since those
# columns need custom markup (badges, pre-escaped names) that df.to_html can't mix with its own
# per-table escape=True/False setting.
#
def _tableHtmlCustom(df, table_id, escape_columns=(), list_columns=(), bool_columns=()):
    if df is None or df.empty:
        return "<p><em>No data.</em></p>"
    display_df = df.fillna("").copy()
    for column in escape_columns:
        display_df[column] = display_df[column].astype(str).apply(html.escape)
    for column in list_columns:
        display_df[column] = display_df[column].apply(_formatNameListCell)
    for column in bool_columns:
        display_df[column] = display_df[column].apply(_formatBoolCell)
    return display_df.to_html(classes="display", table_id=table_id, index=False, border=0, escape=False)


def _dataTableInitScript(table_ids):
    inits = "\n".join(f"  $('#{tid}').DataTable();" for tid in table_ids)
    return f"<script>\n$(document).ready(function() {{\n{inits}\n}});\n</script>"


def _barChartScript(canvas_id, labels, values, label_text):
    return f"""<script>
new Chart(document.getElementById('{canvas_id}').getContext('2d'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(labels)},
    datasets: [{{ label: {json.dumps(label_text)}, data: {json.dumps(values)}, backgroundColor: 'rgba(54, 162, 235, 0.6)' }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }} }}
}});
</script>"""


def _kpiCardsHtml(kpis, extra_labels=None):
    labels = {
        "total_streams": "Total streams",
        "total_projects": "Total projects",
        "distinct_triage_stores": "Distinct triage stores",
        "distinct_component_maps": "Distinct component maps",
        "pct_default_component_map": "% on Default component map",
        "pct_default_triage_store": "% on Default triage store",
        "pct_outdated": "% outdated streams",
        "missing_owner_count": "Streams missing owner",
    }
    if extra_labels:
        labels.update(extra_labels)
    cards = []
    for key, label in labels.items():
        value = kpis.get(key, "n/a")
        if key.startswith("pct_"):
            value = f"{value}%"
        cards.append(f'<div class="kpi-card"><div class="kpi-value">{value}</div><div class="kpi-label">{label}</div></div>')
    return "\n".join(cards)


#
# Builds a bar chart (canvas + script) from a "stream_count" aggregate DataFrame, limited to the
# top_n rows by stream_count so the chart stays readable; full data is still shown in its table.
#
def _chartForAggregate(df, name_column, canvas_id, title, top_n=20, description=None):
    if df is None or df.empty:
        return "", ""
    top = df.sort_values("stream_count", ascending=False).head(top_n)
    labels = top[name_column].astype(str).tolist()
    values = top["stream_count"].tolist()
    canvas_html = f'<div class="chart-box"><h3>{title} {_infoIcon(description)}</h3><canvas id="{canvas_id}"></canvas></div>'
    script = _barChartScript(canvas_id, labels, values, "Stream count")
    return canvas_html, script


def _failedInstancesHtml(failed_instances):
    if not failed_instances:
        return ""
    rows = "".join(
        f"<tr><td>{f.get('label', '')}</td><td>{f.get('error', '')}</td></tr>"
        for f in failed_instances
    )
    heading = _heading("h2", "Failed instances", "failedInstancesTable")
    return f"""
{heading}
<table class="plain"><thead><tr><th>Instance</th><th>Error</th></tr></thead><tbody>{rows}</tbody></table>
"""


def buildHtmlReport(title, report_data, is_aggregated=False):
    kpis = report_data.get("kpis", {})
    streams_df = report_data.get("streams")
    by_triage_store = report_data.get("by_triage_store")
    by_component_map = report_data.get("by_component_map")
    by_project = report_data.get("by_project")
    anomalies = report_data.get("anomalies")
    failed_instances = report_data.get("failed_instances", [])

    chart_html_parts = []
    chart_scripts = []

    triage_chart_html, triage_chart_script = _chartForAggregate(
        by_triage_store, "triageStoreName", "triageStoreChart", "Streams per Triage Store",
        description="Top triage stores by stream count. See the full breakdown in the Triage store usage table below."
    )
    component_chart_html, component_chart_script = _chartForAggregate(
        by_component_map, "componentMapName", "componentMapChart", "Streams per Component Map",
        description="Top component maps by stream count. See the full breakdown in the Component map usage table below."
    )
    chart_html_parts += [triage_chart_html, component_chart_html]
    chart_scripts += [triage_chart_script, component_chart_script]

    if is_aggregated:
        instance_chart_html, instance_chart_script = _chartForAggregate(
            report_data.get("by_instance"), "instance", "instanceChart", "Streams per Instance",
            description="Top instances by stream count across all configured Coverity Connect instances."
        )
        chart_html_parts.append(instance_chart_html)
        chart_scripts.append(instance_chart_script)

    table_ids = ["streamsTable", "projectsTable", "triageStoreTable", "componentMapTable", "anomaliesTable"]
    tables_html = [
        _heading("h2", "Streams", "streamsTable") + _tableHtml(streams_df, "streamsTable"),
        _heading("h2", "Projects summary", "projectsTable") + _tableHtml(by_project, "projectsTable"),
        _heading("h2", "Triage store usage", "triageStoreTable") + _tableHtmlCustom(
            by_triage_store, "triageStoreTable", escape_columns=["triageStoreName"], list_columns=["project_names"]
        ),
        _heading("h2", "Component map usage", "componentMapTable") + _tableHtmlCustom(
            by_component_map, "componentMapTable", escape_columns=["componentMapName"], list_columns=["project_names"]
        ),
        _heading("h2", "Anomalies", "anomaliesTable") + _tableHtml(anomalies, "anomaliesTable"),
    ]

    extra_kpi_labels = None
    if is_aggregated:
        extra_kpi_labels = {
            "cross_instance_projects_count": "Projects shared across instances",
            "cross_instance_streams_count": "Streams shared across instances",
        }
        table_ids += ["crossInstanceProjectsTable", "crossInstanceStreamsTable"]
        tables_html += [
            _heading("h2", "Cross-instance projects", "crossInstanceProjectsTable") + _tableHtmlCustom(
                report_data.get("cross_instance_projects"), "crossInstanceProjectsTable",
                escape_columns=["primaryProjectName"], list_columns=["instances", "triage_store_names", "component_map_names"],
                bool_columns=["triage_store_consistent", "component_map_consistent"],
            ),
            _heading("h2", "Cross-instance streams", "crossInstanceStreamsTable") + _tableHtmlCustom(
                report_data.get("cross_instance_streams"), "crossInstanceStreamsTable",
                escape_columns=["name"], list_columns=["instances", "triage_store_names", "component_map_names"],
                bool_columns=["triage_store_consistent", "component_map_consistent"],
            ),
        ]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<link rel="stylesheet" href="{DATATABLES_CSS}">
<script src="{JQUERY_JS}"></script>
<script src="{DATATABLES_JS}"></script>
<script src="{CHARTJS_JS}"></script>
<style>
  body {{ font-family: Arial, sans-serif; margin: 20px; }}
  .kpi-row {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 24px; }}
  .kpi-card {{ background: #f4f6f8; border-radius: 6px; padding: 12px 18px; min-width: 150px; text-align: center; }}
  .kpi-value {{ font-size: 1.6em; font-weight: bold; }}
  .kpi-label {{ font-size: 0.85em; color: #555; }}
  .chart-row {{ display: flex; flex-wrap: wrap; gap: 24px; margin-bottom: 24px; }}
  .chart-box {{ flex: 1 1 400px; max-width: 500px; }}
  table.plain {{ border-collapse: collapse; }}
  table.plain td, table.plain th {{ border: 1px solid #ccc; padding: 4px 8px; }}
  h2 {{ margin-top: 32px; }}
  table.display td {{ white-space: normal; word-break: break-word; }}
  .info-icon {{
    display: inline-block; width: 16px; height: 16px; line-height: 16px;
    border-radius: 50%; background: #607d8b; color: #fff; font-size: 11px;
    font-weight: bold; font-style: italic; text-align: center; cursor: help; user-select: none;
  }}
  .badge-yes {{
    display: inline-block; padding: 1px 8px; border-radius: 10px;
    background: #d4edda; color: #155724; font-size: 0.85em; font-weight: bold;
  }}
  .badge-no {{
    display: inline-block; padding: 1px 8px; border-radius: 10px;
    background: #f8d7da; color: #721c24; font-size: 0.85em; font-weight: bold;
  }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="kpi-row">
{_kpiCardsHtml(kpis, extra_kpi_labels)}
</div>
<div class="chart-row">
{''.join(chart_html_parts)}
</div>
{_failedInstancesHtml(failed_instances)}
{''.join(tables_html)}
{''.join(chart_scripts)}
{_dataTableInitScript(table_ids)}
</body>
</html>
"""
    return html


def writeHtml(title, report_data, output_dir, filename, is_aggregated=False):
    path = os.path.join(output_dir, filename)
    html = buildHtmlReport(title, report_data, is_aggregated=is_aggregated)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    logging.info(f"Wrote HTML report: {path}")
    return path
