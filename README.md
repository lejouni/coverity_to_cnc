# coverity_to_cnc

Current version: see `__version__` in [config.py](config.py), or run `python .\main.py --version`.

Reports on Coverity Connect streams, triage stores and component maps across one or more instances,
and (in multi-instance mode) flags projects/streams whose name collides across instances. Outputs
CSV and a self-contained interactive HTML report (DataTables + Chart.js) per instance, plus an
aggregated cross-instance report.

## Setup

```powershell
git clone https://github.com/lejouni/coverity_to_cnc.git
cd coverity_to_cnc
pip install -r requirements.txt
```

## Running against a single instance

```powershell
python .\main.py --url="https://coverity.example.com:443" --username="svc_account" --password="..." --output_dir="exports"
```

`--username`/`--password` fall back to the `COVERITY_USERNAME`/`COVERITY_PASSWORD` environment
variables if omitted.

## Running against multiple instances

1. Copy `instances.example.json` to `instances.json` and fill in your instances. Each entry:
   - `label` (optional) - a nickname to use in filenames/reports instead of the URL's host:port.
   - `url`, `username` - instance connection details.
   - `password_env` - name of the environment variable holding that instance's password (the
     password itself is never stored in the file).
   - `verify_ssl` - set to `false` only for instances using a self-signed/internal certificate.
2. Set each referenced password environment variable, e.g.:
   ```powershell
   $env:COVERITY_PASSWORD_FIELDTEST = "..."
   ```
3. Run:
   ```powershell
   python .\main.py --instances_file="./instances.json" --output_dir="exports"
   ```

`instances.json` is git-ignored since it contains internal hostnames/usernames - only
`instances.example.json` is meant to be shared/committed.

## Aggregating runs made at different times/networks

If you can only reach one instance at a time (e.g. different VPNs), run the script once per
instance with `--url` as usual - each successful run writes a `coverity_streams_data_<label>_<timestamp>.json`
file alongside its CSV/HTML report. Once you have one of these data files per instance (copy them
into the same folder if they came from different runs/output dirs), build the aggregated
cross-instance report from them directly, without contacting any instance:

```powershell
python .\main.py --aggregate_from_dir="exports" --output_dir="exports"
```

## Useful flags

| Flag | Purpose |
|---|---|
| `--workers N` | Fetch N instances concurrently (default: 1, sequential). |
| `--log_level DEBUG` | Verbose logging, including every API call made. |
| `--projects_file path.json` | Offline mode: read projects/streams from a local JSON file instead of the API. |

## Output

Each run writes, per instance, a CSV and HTML report into `--output_dir` (default `.`), plus
`coverity_streams_data_<label>_<timestamp>.json` and `coverity_diagnostics_<label>_<timestamp>.json`
files used by `--aggregate_from_dir` (see above). In multi-instance mode, or when using
`--aggregate_from_dir`, it additionally writes an aggregated `..._ALL_...` CSV/HTML covering all
instances together, including the cross-instance project/stream name collision tables.

Each instance's HTML report also includes a "Server diagnostics" section pulled from
`GET {url}/diagnostics/overview.json` (commit queue state, user/group/stream/project counts and
database size). If that call fails, the section is simply omitted - it never aborts the run.

Projects with zero streams are still counted/listed (from `/api/v2/projects` directly). Streams
with no parent project are detected via a supplemental `GET {url}/config/projects.json` call
(skipped in `--projects_file` offline mode) - they're read from that response's built-in
"Other Streams" pseudo-project (id -1) and listed in the "Orphan streams" table; that call
failing is also non-fatal.

Triage stores with no associated project/stream are detected via a supplemental
`GET {url}/config/triagestores.json` call (skipped in `--projects_file` offline mode) - a top-level
entry whose `children` list is empty is reported in the "Unused triage stores" table; that call
failing is also non-fatal.

Component maps with no associated stream are detected via a supplemental
`GET {url}/config/componentmaps.json` call (skipped in `--projects_file` offline mode) - a
top-level entry whose own `streams` list is empty is reported in the "Unused component maps"
table; that call failing is also non-fatal.

Every report (CSV and HTML, per-instance and aggregated) is stamped with the `coverity_to_cnc`
version and a generation timestamp, so you can tell what produced a given file and when. The
aggregated `..._ALL_...` HTML report also links back to each instance's own HTML report, so you
can jump from a cross-instance table straight to the full per-instance detail.


## License

MIT - see [LICENSE](LICENSE).
