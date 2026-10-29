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
    "orphanStreamsTable": "Streams with no primary project, read from the built-in 'Other Streams' pseudo-project (id -1) in GET {instance}/config/projects.json.",
    "unusedTriageStoresTable": "Triage stores whose 'children' list is empty in GET {instance}/config/triagestores.json, i.e. not attached to any project/stream - cleanup candidates.",
    "unusedComponentMapsTable": "Component maps whose own 'streams' list is empty in GET {instance}/config/componentmaps.json, i.e. not attached to any stream - cleanup candidates.",
    "failedInstancesTable": "Instances that could not be processed, e.g. due to connection, authentication or API errors.",
    "crossInstanceProjectsTable": "Project names that exist in more than one instance, with the instances they appear in and whether every instance uses the same triage store/component map name for that project's streams. 'No' under *_consistent flags a naming collision worth reviewing before consolidating into one CNC instance.",
    "crossInstanceStreamsTable": "Stream names that exist in more than one instance, with the instances they appear in and whether every instance uses the same triage store/component map name for that stream. 'No' under *_consistent flags a naming collision worth reviewing before consolidating into one CNC instance.",
    "diagnosticsTable": "Server diagnostics overview (GET /diagnostics/overview.json) per instance: commit queue state, object counts and database size.",
}


#
# Renders a small "i" badge whose native title tooltip explains what the section shows.
#
def _infoIcon(description):
    if not description:
        return ""
    return f'<span class="info-icon" title="{html.escape(description)}">i</span>'


#
# Wraps a table/body in a styled card with a heading (bearing an anchor id the quick-nav links
# jump to) and the section's info-icon tooltip.
#
def _section(text, table_id, body_html):
    anchor_id = f"section-{table_id}"
    heading = f'<h2 id="{anchor_id}">{html.escape(text)} {_infoIcon(TABLE_DESCRIPTIONS.get(table_id, ""))}</h2>'
    return f'<section class="card">\n{heading}\n{body_html}\n</section>'


def _tableHtml(df, table_id):
    if df is None or df.empty:
        return "<p><em>No data.</em></p>"
    table = df.fillna("").to_html(classes="display", table_id=table_id, index=False, border=0, escape=True)
    return f'<div class="table-scroll">{table}</div>'


#
# Renders a byte count as a human-readable size (e.g. 66454042303 -> "61.9 GB").
#
def _formatBytes(num_bytes):
    try:
        value = float(num_bytes)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


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
    table = display_df.to_html(classes="display", table_id=table_id, index=False, border=0, escape=False)
    return f'<div class="table-scroll">{table}</div>'


#
# A single <table> (header + body together) scrolling as one unit avoids the classic DataTables
# scrollX bug where its separate header/body clone tables drift out of alignment on narrower
# tables - plain CSS overflow keeps header cells and data cells permanently in sync.
#
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
# Flags a mismatch between what the /api/v2/projects call could see (kpis, permission-scoped to
# the API account) and the server-wide diagnostics counts (unfiltered DB totals), since the two
# numbers are expected to only match when the API account can see every project/stream.
#
def _diagnosticsMismatchNote(kpis, diagnostics):
    notes = []
    for count_key, server_key, noun in (("total_projects", "numProjects", "projects"), ("total_streams", "numStreams", "streams")):
        api_count = kpis.get(count_key)
        server_count = diagnostics.get(server_key)
        if api_count is not None and server_count is not None and api_count != server_count:
            notes.append(f"{api_count} {noun} via API vs {server_count} reported by server diagnostics")
    if not notes:
        return ""
    message = "; ".join(notes) + (
        ". This is expected when the API account doesn't have a role on every project - the REST API "
        "only returns what that account can see, while diagnostics counts everything in the database."
    )
    return f'<p class="diagnostics-note">{html.escape(message)}</p>'


#
# Renders the server diagnostics overview (GET /diagnostics/overview.json) for one instance as
# KPI cards. Returns "" if diagnostics couldn't be fetched, so the section is simply omitted.
#
def _diagnosticsCardsHtml(diagnostics, kpis=None):
    if not diagnostics:
        return ""
    labels = {
        "numStreams": "Streams (server-reported)",
        "numProjects": "Projects (server-reported)",
        "numTriageStores": "Triage stores",
        "numComponentMaps": "Component maps",
        "numUsers": "Users",
        "numGroups": "Groups",
        "pendingCommits": "Pending commits",
        "runningCommits": "Running commits",
        "numPendingEmails": "Pending emails",
        "dbSize": "Database size",
    }
    bool_labels = {
        "commitGateOpen": "Commit gate open",
        "backUpInProgress": "Backup in progress",
        "skeletonizationInProgress": "Skeletonization in progress",
    }
    cards = []
    for key, label in labels.items():
        if key not in diagnostics:
            continue
        value = diagnostics[key]
        if key == "dbSize":
            value = _formatBytes(value)
        cards.append(f'<div class="kpi-card"><div class="kpi-value">{value}</div><div class="kpi-label">{label}</div></div>')
    for key, label in bool_labels.items():
        if key not in diagnostics:
            continue
        cards.append(f'<div class="kpi-card">{_formatBoolCell(diagnostics[key])}<div class="kpi-label">{label}</div></div>')
    if not cards:
        return ""
    mismatch_note = _diagnosticsMismatchNote(kpis or {}, diagnostics)
    body = f'<div class="kpi-row">\n{"".join(cards)}\n</div>\n{mismatch_note}'
    return _section("Server diagnostics", "diagnosticsTable", body)


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


#
# Renders the per-instance diagnostics_by_instance DataFrame (aggregated report only), with
# dbSize formatted as a human-readable size and the queue/maintenance flags as badges.
#
def _diagnosticsTableHtml(df, table_id):
    if df is None or df.empty:
        return "<p><em>No data.</em></p>"
    display_df = df.copy()
    display_df["dbSize"] = display_df["dbSize"].apply(_formatBytes)
    return _tableHtmlCustom(
        display_df, table_id, escape_columns=["instance"],
        bool_columns=["commitGateOpen", "backUpInProgress", "skeletonizationInProgress"],
    )


def _failedInstancesHtml(failed_instances):
    if not failed_instances:
        return ""
    rows = "".join(
        f"<tr><td>{f.get('label', '')}</td><td>{f.get('error', '')}</td></tr>"
        for f in failed_instances
    )
    body = f'<table class="plain"><thead><tr><th>Instance</th><th>Error</th></tr></thead><tbody>{rows}</tbody></table>'
    return _section("Failed instances", "failedInstancesTable", body)


#
# Renders the "generated by coverity_to_cnc vX.Y.Z on <timestamp>" subtitle line.
#
def _reportMetaHtml(version, generated_at):
    if not version and not generated_at:
        return ""
    parts = []
    if version:
        parts.append(f"coverity_to_cnc v{html.escape(version)}")
    if generated_at:
        parts.append(f"generated {html.escape(generated_at)}")
    return f'<p class="report-meta">{" &middot; ".join(parts)}</p>'


#
# Renders links to each instance's own HTML report (aggregated report only) as a row of pills.
# instance_reports is a list of (label, relative_path) tuples built by
# main.buildAggregateOnlyReport from the coverity_diagnostics_*.json/coverity_streams_report_*.html
# filename pairs written together per instance run.
#
def _instanceReportsHtml(instance_reports):
    if not instance_reports:
        return ""
    items = "".join(
        f'<a class="instance-link" href="{html.escape(path.replace(chr(92), "/"))}">{html.escape(label)}</a>'
        for label, path in instance_reports
    )
    return f'<div class="instance-links"><span class="instance-links-label">Instance reports:</span>{items}</div>'


#
# Renders the pill-style quick navigation bar linking to each rendered section's anchor id.
#
def _quickNavHtml(nav_items):
    if not nav_items:
        return ""
    links = "".join(f'<a href="#{anchor}">{html.escape(text)}</a>' for text, anchor in nav_items)
    return f'<nav class="quick-nav">{links}</nav>'


def buildHtmlReport(title, report_data, is_aggregated=False):
    kpis = report_data.get("kpis", {})
    streams_df = report_data.get("streams")
    by_triage_store = report_data.get("by_triage_store")
    by_component_map = report_data.get("by_component_map")
    by_project = report_data.get("by_project")
    orphan_streams = report_data.get("orphan_streams")
    unused_triage_stores = report_data.get("unused_triage_stores")
    unused_component_maps = report_data.get("unused_component_maps")
    failed_instances = report_data.get("failed_instances", [])

    nav_items = [("Overview", "section-overview")]

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
    chart_html_parts += [part for part in (triage_chart_html, component_chart_html) if part]
    chart_scripts += [triage_chart_script, component_chart_script]

    if is_aggregated:
        instance_chart_html, instance_chart_script = _chartForAggregate(
            report_data.get("by_instance"), "instance", "instanceChart", "Streams per Instance",
            description="Top instances by stream count across all configured Coverity Connect instances."
        )
        if instance_chart_html:
            chart_html_parts.append(instance_chart_html)
        chart_scripts.append(instance_chart_script)

    charts_section = ""
    if chart_html_parts:
        charts_section = _section("Usage charts", "charts", f'<div class="chart-row">\n{"".join(chart_html_parts)}\n</div>')
        nav_items.append(("Usage charts", "section-charts"))

    diagnostics_section = ""
    diagnostics_table_ids = []
    if is_aggregated:
        diagnostics_df = report_data.get("diagnostics_by_instance")
        if diagnostics_df is not None and not diagnostics_df.empty:
            diagnostics_section = _section("Server diagnostics", "diagnosticsTable", _diagnosticsTableHtml(diagnostics_df, "diagnosticsTable"))
            diagnostics_table_ids = ["diagnosticsTable"]
    else:
        diagnostics_section = _diagnosticsCardsHtml(report_data.get("diagnostics"), kpis)
    if diagnostics_section:
        nav_items.append(("Server diagnostics", "section-diagnosticsTable"))

    table_ids = ["streamsTable", "projectsTable", "triageStoreTable", "componentMapTable", "orphanStreamsTable", "unusedTriageStoresTable", "unusedComponentMapsTable"] + diagnostics_table_ids
    table_sections = [
        ("Streams", "streamsTable", _tableHtml(streams_df, "streamsTable")),
        ("Projects summary", "projectsTable", _tableHtml(by_project, "projectsTable")),
        ("Triage store usage", "triageStoreTable", _tableHtmlCustom(
            by_triage_store, "triageStoreTable", escape_columns=["triageStoreName"], list_columns=["project_names"]
        )),
        ("Component map usage", "componentMapTable", _tableHtmlCustom(
            by_component_map, "componentMapTable", escape_columns=["componentMapName"], list_columns=["project_names"]
        )),
        ("Orphan streams", "orphanStreamsTable", _tableHtml(orphan_streams, "orphanStreamsTable")),
        ("Unused triage stores", "unusedTriageStoresTable", _tableHtml(unused_triage_stores, "unusedTriageStoresTable")),
        ("Unused component maps", "unusedComponentMapsTable", _tableHtml(unused_component_maps, "unusedComponentMapsTable")),
    ]

    extra_kpi_labels = None
    if is_aggregated:
        extra_kpi_labels = {
            "cross_instance_projects_count": "Projects shared across instances",
            "cross_instance_streams_count": "Streams shared across instances",
        }
        table_ids += ["crossInstanceProjectsTable", "crossInstanceStreamsTable"]
        table_sections += [
            ("Cross-instance projects", "crossInstanceProjectsTable", _tableHtmlCustom(
                report_data.get("cross_instance_projects"), "crossInstanceProjectsTable",
                escape_columns=["primaryProjectName"], list_columns=["instances", "triage_store_names", "component_map_names"],
                bool_columns=["triage_store_consistent", "component_map_consistent"],
            )),
            ("Cross-instance streams", "crossInstanceStreamsTable", _tableHtmlCustom(
                report_data.get("cross_instance_streams"), "crossInstanceStreamsTable",
                escape_columns=["name"], list_columns=["instances", "triage_store_names", "component_map_names"],
                bool_columns=["triage_store_consistent", "component_map_consistent"],
            )),
        ]

    nav_items += [(text, f"section-{table_id}") for text, table_id, _ in table_sections]
    tables_html = [_section(text, table_id, body) for text, table_id, body in table_sections]

    failed_instances_html = _failedInstancesHtml(failed_instances)
    if failed_instances_html:
        nav_items.append(("Failed instances", "section-failedInstancesTable"))

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
  :root {{
    --bg: #eef1f6; --card-bg: #ffffff; --text: #1f2933; --muted: #5b6b79;
    --accent: #2563eb; --accent-soft: #e8f0fe; --border: #e2e8f0; --radius: 12px;
    --shadow: 0 1px 3px rgba(15, 23, 42, 0.08), 0 1px 2px rgba(15, 23, 42, 0.06);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    margin: 0; background: var(--bg); color: var(--text);
  }}
  .page {{ max-width: 1400px; margin: 0 auto; padding: 32px 24px 64px; }}
  .page-header h1 {{ font-size: 1.6em; margin: 0 0 14px; }}
  .page-header .report-meta {{ font-size: 0.85em; color: var(--muted); margin: -8px 0 16px; }}
  .instance-links {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 20px; }}
  .instance-links-label {{ font-size: 0.85em; color: var(--muted); }}
  .instance-link {{
    background: var(--accent-soft); color: var(--accent); border-radius: 999px;
    padding: 5px 14px; font-size: 0.85em; text-decoration: none; font-weight: 600;
  }}
  .instance-link:hover {{ background: var(--accent); color: #fff; }}
  .quick-nav {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 24px; }}
  .quick-nav a {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 999px;
    padding: 6px 14px; font-size: 0.85em; color: var(--accent); text-decoration: none; font-weight: 500;
  }}
  .quick-nav a:hover {{ background: var(--accent-soft); border-color: var(--accent); }}
  .card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius);
    box-shadow: var(--shadow); padding: 20px 24px; margin-bottom: 24px;
  }}
  .card h2 {{
    margin: 0 0 16px; font-size: 1.15em; padding-bottom: 10px; border-bottom: 1px solid var(--border);
  }}
  .kpi-row {{ display: flex; flex-wrap: wrap; gap: 14px; }}
  .kpi-card {{
    background: var(--card-bg); border: 1px solid var(--border); border-left: 4px solid var(--accent);
    border-radius: 10px; padding: 14px 18px; min-width: 160px; flex: 1 1 160px;
    box-shadow: var(--shadow); transition: transform .15s ease, box-shadow .15s ease;
  }}
  .kpi-card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 16px rgba(15, 23, 42, 0.12); }}
  .kpi-value {{ font-size: 1.7em; font-weight: 700; }}
  .kpi-label {{ font-size: 0.82em; color: var(--muted); margin-top: 4px; }}
  .chart-row {{ display: flex; flex-wrap: wrap; gap: 24px; }}
  .chart-box {{ flex: 1 1 380px; max-width: 480px; }}
  .chart-box h3 {{ margin: 0 0 12px; font-size: 1em; color: var(--muted); font-weight: 600; }}
  table.plain {{ border-collapse: collapse; width: 100%; }}
  table.plain td, table.plain th {{ border: 1px solid var(--border); padding: 6px 10px; text-align: left; }}
  table.plain th {{ background: #f8fafc; }}
  table.dataTable td {{ white-space: nowrap; }}
  .table-scroll {{ overflow-x: auto; width: 100%; }}
  div.dataTables_wrapper {{ margin-top: 4px; }}
  table.dataTable thead th {{ background: #f8fafc; border-bottom: 2px solid var(--border) !important; }}
  table.dataTable.display tbody tr:hover > * {{ background-color: var(--accent-soft) !important; }}
  .info-icon {{
    display: inline-block; width: 16px; height: 16px; line-height: 16px;
    border-radius: 50%; background: var(--muted); color: #fff; font-size: 11px;
    font-weight: bold; font-style: italic; text-align: center; cursor: help; user-select: none;
  }}
  .badge-yes {{
    display: inline-block; padding: 1px 8px; border-radius: 10px;
    background: #dcfce7; color: #166534; font-size: 0.85em; font-weight: bold;
  }}
  .badge-no {{
    display: inline-block; padding: 1px 8px; border-radius: 10px;
    background: #fee2e2; color: #991b1b; font-size: 0.85em; font-weight: bold;
  }}
  .diagnostics-note {{
    background: #fef9c3; color: #854d0e; border: 1px solid #fde68a;
    border-radius: 10px; padding: 10px 16px; margin-top: 14px; margin-bottom: 0; font-size: 0.9em;
  }}
</style>
</head>
<body>
<div class="page">
<header class="page-header">
<h1>{title}</h1>
{_reportMetaHtml(report_data.get("version"), report_data.get("generated_at"))}
{_instanceReportsHtml(report_data.get("instance_reports")) if is_aggregated else ""}
</header>
{_quickNavHtml(nav_items)}
{_section("Overview", "overview", f'<div class="kpi-row">{_kpiCardsHtml(kpis, extra_kpi_labels)}</div>')}
{diagnostics_section}
{charts_section}
{failed_instances_html}
{''.join(tables_html)}
</div>
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
