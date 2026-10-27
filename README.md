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
| `--skip_project_enrichment` | Skip the extra per-project detail lookup (faster, less data). |
| `--log_level DEBUG` | Verbose logging, including every API call made. |
| `--streams_file path.json` | Offline mode: read streams from a local JSON file instead of the API. |

## Output

Each run writes, per instance, a CSV and HTML report into `--output_dir` (default `.`), plus a
`coverity_streams_data_<label>_<timestamp>.json` file used by `--aggregate_from_dir` (see above).
In multi-instance mode, or when using `--aggregate_from_dir`, it additionally writes an aggregated
`..._ALL_...` CSV/HTML covering all instances together, including the cross-instance project/stream
name collision tables.

## License

MIT - see [LICENSE](LICENSE).
