#!/usr/bin/env python3
"""
Dataset Sync Script — DEV-543

Reads an eval.yaml file (per DEV-321 schema) and syncs its items to a Langfuse
dataset. Upserts items by id, archives items no longer in the file, and prints
a version timestamp that pins experiment runs to the exact dataset state.

Usage:
    python src/dataset_sync.py --file skills/linear-create/eval.yaml
    python src/dataset_sync.py --file eval.yaml --dry-run
    python src/dataset_sync.py --file eval.yaml --langfuse-host http://10.18.32.57:3000

Credentials:
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables.
    Same env config as eval_harness.py and eval_report.py.

eval.yaml schema (DEV-321):
    dataset: <langfuse-dataset-name>
    items:
      - id: <unique-string-id>
        input: <prompt text sent to the agent>
        expected_output: <what a correct response looks like>

Output:
    Prints the dataset version timestamp (ISO-8601 UTC) on success.
    The eval harness can use this with get_dataset(version=<timestamp>)
    to run experiments against the exact dataset state from this sync.
"""

import argparse
import base64
import os
import sys
import time
from datetime import datetime, timezone

import requests
import yaml


# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_LANGFUSE_HOST = "http://10.18.32.57:3000"
API_BASE = "/api/public"
# Brief pause after writes so the Langfuse server has fully processed the
# version bump before we read it back. 2s is conservative; 1s usually suffices.
POST_SYNC_SETTLE_SECONDS = 2

# Known keys for validation warnings
KNOWN_TOP_LEVEL_KEYS = {"dataset", "items"}
KNOWN_ITEM_KEYS = {"id", "input", "expected_output"}

# Separator for namespaced Langfuse item IDs. Langfuse dataset item IDs are
# project-wide unique (see langfuse#2167), so we prefix the API ID with the
# dataset name to prevent collisions when two datasets use the same logical
# item ID (e.g. both have `id: happy-path`). The YAML-facing ID stays as-is.
ID_SEPARATOR = ":"


# ── Helpers ──────────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def make_auth_header(public_key, secret_key):
    """Build the Basic auth header from Langfuse API keys."""
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def make_api_id(dataset_name, item_id):
    """Build a project-wide unique Langfuse API ID from dataset name + item ID.

    Langfuse dataset item IDs are project-wide unique (langfuse#2167), not
    per-dataset. Without namespacing, two datasets sharing the same logical
    item ID (e.g. `happy-path`) would collide and corrupt each other.
    """
    return f"{dataset_name}{ID_SEPARATOR}{item_id}"


def strip_dataset_prefix(api_id, dataset_name):
    """Strip the dataset prefix from a namespaced Langfuse API ID.

    Returns the logical item ID. If the API ID doesn't have the expected
    prefix, returns it as-is (for backwards compat with non-namespaced items).
    """
    prefix = f"{dataset_name}{ID_SEPARATOR}"
    if api_id.startswith(prefix):
        return api_id[len(prefix):]
    return api_id


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader subclass that rejects duplicate mapping keys.

    yaml.safe_load silently keeps the last value when a key appears twice,
    which can mask copy-paste errors — e.g. a duplicated `items:` key where
    the second value is empty would archive the entire dataset.
    """

    def construct_mapping(self, node, deep=False):
        seen_keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen_keys:
                raise yaml.constructor.ConstructorError(
                    None, None,
                    f"Duplicate key '{key}' found in YAML mapping",
                    key_node.start_mark,
                )
            seen_keys.add(key)
        return super().construct_mapping(node, deep=deep)


def parse_eval_yaml(path):
    """Parse an eval.yaml file and return (dataset_name, items_list).

    items_list is a list of dicts with keys: id, input, expected_output.
    Exits with an error if the file is malformed or missing required fields.
    Warns (but does not fail) on unknown keys that may indicate typos.

    Whitespace is stripped only for validation (emptiness checks). The
    original unstripped values are preserved in the returned items so that
    prompts with intentional leading/trailing whitespace (e.g. block scalars,
    code samples) are uploaded verbatim.
    """
    try:
        with open(path, "r") as f:
            data = yaml.load(f, Loader=UniqueKeyLoader)
    except FileNotFoundError:
        log(f"File not found: {path}", "ERROR")
        sys.exit(1)
    except yaml.YAMLError as e:
        log(f"YAML parse error in {path}: {e}", "ERROR")
        sys.exit(1)

    if not isinstance(data, dict):
        log(f"Expected a YAML mapping at top level, got {type(data).__name__}", "ERROR")
        sys.exit(1)

    # ── Warn on unknown top-level keys ───────────────────────────────────
    unknown_top = set(data.keys()) - KNOWN_TOP_LEVEL_KEYS
    if unknown_top:
        log(f"Unknown top-level key(s): {', '.join(sorted(unknown_top))} — may be a typo or staged field", "WARN")

    # ── Validate dataset name ────────────────────────────────────────────
    dataset_name = data.get("dataset")
    if not dataset_name:
        log("Missing required field 'dataset' in eval.yaml", "ERROR")
        sys.exit(1)
    if not isinstance(dataset_name, str):
        log(f"'dataset' must be a string, got {type(dataset_name).__name__}", "ERROR")
        sys.exit(1)
    if not dataset_name.strip():
        log("'dataset' must not be empty or whitespace-only", "ERROR")
        sys.exit(1)
    dataset_name = dataset_name.strip()

    # ── Validate items ──────────────────────────────────────────────────
    # Require 'items' key to be present — omitting it is likely a mistake,
    # not an intentional empty dataset. Use `items: []` for the latter.
    if "items" not in data:
        log("Missing required field 'items' in eval.yaml (use 'items: []' for an empty dataset)", "ERROR")
        sys.exit(1)

    items = data["items"]
    if not isinstance(items, list):
        log("'items' must be a list", "ERROR")
        sys.exit(1)

    parsed = []
    seen_ids = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            log(f"Item {i} is not a mapping: {item}", "ERROR")
            sys.exit(1)

        # Warn on unknown item keys (may indicate typos or staged fields)
        unknown_keys = set(item.keys()) - KNOWN_ITEM_KEYS
        if unknown_keys:
            log(f"Item {i} has unknown key(s): {', '.join(sorted(unknown_keys))} — may be a typo or staged field", "WARN")

        # ── Validate id ─────────────────────────────────────────────────
        item_id = item.get("id")
        if item_id is None:
            log(f"Item {i} missing required 'id' field", "ERROR")
            sys.exit(1)
        if not isinstance(item_id, str):
            item_id = str(item_id)
        if not item_id.strip():
            log(f"Item {i} has empty or whitespace-only 'id'", "ERROR")
            sys.exit(1)
        item_id = item_id.strip()
        if item_id in seen_ids:
            log(f"Duplicate item id '{item_id}' (item {i}) — ids must be unique after string conversion", "ERROR")
            sys.exit(1)
        seen_ids.add(item_id)

        # ── Validate input ──────────────────────────────────────────────
        input_value = item.get("input")
        if input_value is None:
            log(f"Item '{item_id}' missing required 'input' field", "ERROR")
            sys.exit(1)
        if not isinstance(input_value, str):
            log(f"Item '{item_id}' input must be a string, got {type(input_value).__name__}", "ERROR")
            sys.exit(1)
        if not input_value.strip():
            log(f"Item '{item_id}' has empty or whitespace-only 'input'", "ERROR")
            sys.exit(1)
        # Preserve original (unstripped) input for upload

        # ── Validate expected_output ────────────────────────────────────
        expected = item.get("expected_output")
        if expected is None:
            log(f"Item '{item_id}' missing required 'expected_output' field", "ERROR")
            sys.exit(1)
        if not isinstance(expected, str):
            log(f"Item '{item_id}' expected_output must be a string, got {type(expected).__name__}", "ERROR")
            sys.exit(1)
        if not expected.strip():
            log(f"Item '{item_id}' has empty or whitespace-only 'expected_output'", "ERROR")
            sys.exit(1)
        # Preserve original (unstripped) expected_output for upload

        parsed.append({
            "id": item_id,
            "input": input_value,
            "expected_output": expected,
        })

    return dataset_name, parsed


def fetch_existing_items(host, auth_header, dataset_name):
    """Fetch all dataset items from Langfuse for the given dataset.

    Returns a dict mapping the **namespaced API ID** → item dict.
    Uses datasetName query param (datasetId has a known bug — see langfuse#13285).
    """
    url = f"{host}{API_BASE}/dataset-items"
    items_by_id = {}
    page = 1

    while True:
        params = {"datasetName": dataset_name, "page": page, "limit": 100}
        resp = requests.get(url, params=params, headers=auth_header, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", [])
        for item in data:
            items_by_id[item["id"]] = item
        # Check if there are more pages
        meta = body.get("meta", {})
        if page >= meta.get("totalPages", 1):
            break
        page += 1

    return items_by_id


def upsert_item(host, auth_header, dataset_name, item):
    """Upsert a single dataset item to Langfuse via POST /api/public/dataset-items.

    The API ID is namespaced as `{dataset_name}:{item_id}` to avoid
    project-wide ID collisions (see langfuse#2167).
    """
    url = f"{host}{API_BASE}/dataset-items"
    api_id = make_api_id(dataset_name, item["id"])
    payload = {
        "id": api_id,
        "datasetName": dataset_name,
        "input": item["input"],
        "expectedOutput": item["expected_output"],
        "status": "ACTIVE",
        "source": "eval-yaml-sync",
    }
    resp = requests.post(url, json=payload, headers=auth_header, timeout=15)
    resp.raise_for_status()
    return resp.json()


def archive_item(host, auth_header, dataset_name, item_id):
    """Archive a dataset item by upserting with status=ARCHIVED.

    The `item_id` here is the namespaced API ID.
    """
    url = f"{host}{API_BASE}/dataset-items"
    payload = {
        "id": item_id,
        "datasetName": dataset_name,
        "status": "ARCHIVED",
        "source": "eval-yaml-sync",
    }
    resp = requests.post(url, json=payload, headers=auth_header, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_dataset_version_timestamp():
    """Return the current UTC time as the dataset version timestamp.

    After all upserts and archives settle, use the current UTC time as the
    version pin. This is guaranteed to postdate all write operations,
    including archives that may not be reflected in active-item timestamps.

    Note: This relies on the sync host and Langfuse server having closely
    synchronized clocks (both use NTP on the same LAN). If the hosts have
    significant clock skew, the pin could precede server-side write
    timestamps. For environments with clock skew concerns, parse the
    `Date` header from the last API response instead.
    """
    return datetime.now(timezone.utc)


def try_dry_run_archive_preview(langfuse_host, dataset_name, yaml_ids):
    """Attempt a read-only fetch to preview archive candidates.

    If credentials are available, fetches existing items from Langfuse and
    reports which ones would be archived. If credentials are absent, skips
    silently — dry-run should work offline without API secrets.

    Note: This performs a read-only GET request when credentials are present.
    Dry-run never makes write calls, but may make read calls for archive
    preview. Use --no-preview to skip this entirely.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")

    if not public_key or not secret_key:
        log("Cannot preview archive candidates — LANGFUSE credentials not set (dry-run works offline)", "WARN")
        return

    try:
        auth_header = make_auth_header(public_key, secret_key)
        existing = fetch_existing_items(langfuse_host, auth_header, dataset_name)
        # Build set of namespaced API IDs that exist in Langfuse
        existing_api_ids = set(existing.keys())
        # Build set of namespaced API IDs that will exist after sync
        yaml_api_ids = {make_api_id(dataset_name, logical_id) for logical_id in yaml_ids}
        to_archive = existing_api_ids - yaml_api_ids
        to_archive_active = {
            api_id for api_id in to_archive
            if existing.get(api_id, {}).get("status", "ACTIVE").upper() == "ACTIVE"
        }

        if to_archive_active:
            log(f"Would archive {len(to_archive_active)} items (in Langfuse but not in eval.yaml):")
            for api_id in sorted(to_archive_active):
                logical_id = strip_dataset_prefix(api_id, dataset_name)
                log(f"  [archive] {logical_id}")
        else:
            log("No items would be archived — dataset items are a subset of eval.yaml")
    except requests.RequestException as e:
        log(f"Could not fetch existing items for archive preview: {e}", "WARN")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sync an eval.yaml file to a Langfuse dataset (DEV-543)"
    )
    parser.add_argument(
        "--file", required=True,
        help="Path to the eval.yaml file to sync",
    )
    parser.add_argument(
        "--langfuse-host", default=DEFAULT_LANGFUSE_HOST,
        help=f"Langfuse host URL (default: {DEFAULT_LANGFUSE_HOST})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and show sync plan without making write calls (read-only GET for archive preview if credentials available)",
    )
    parser.add_argument(
        "--no-preview", action="store_true",
        help="Skip read-only archive preview in dry-run mode (no API calls at all)",
    )
    args = parser.parse_args()

    # ── Parse eval.yaml (no credentials needed for dry-run) ───────────────
    dataset_name, yaml_items = parse_eval_yaml(args.file)
    yaml_api_ids = {make_api_id(dataset_name, item["id"]) for item in yaml_items}

    log(f"Parsed eval.yaml: dataset='{dataset_name}', {len(yaml_items)} items")

    if args.dry_run:
        log("=== DRY RUN ===")
        for item in yaml_items:
            preview = item["input"][:80].replace("\n", " ")
            log(f"  [upsert] {item['id']}: {preview}...")
        log(f"Would upsert {len(yaml_items)} items to dataset '{dataset_name}'")

        # Attempt read-only archive preview if credentials are available
        # and --no-preview was not passed
        if not args.no_preview:
            try_dry_run_archive_preview(args.langfuse_host, dataset_name, {item["id"] for item in yaml_items})
        else:
            log("Archive preview skipped (--no-preview)")

        log("No write calls made.")
        return

    # ── Validate credentials (after dry-run so offline preview works) ────
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")

    if not public_key or not secret_key:
        log("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables are required.", "ERROR")
        sys.exit(1)

    auth_header = make_auth_header(public_key, secret_key)

    # ── Upsert items first (no dependency on existing state) ─────────────
    # Upserting before fetching ensures our writes are visible to any
    # concurrent sync that fetches after us. This also means the subsequent
    # fetch sees a snapshot that includes our own upserts, so we only archive
    # items that are genuinely stale — not items a concurrent sync just added.
    upserted = 0
    failed = 0
    for item in yaml_items:
        try:
            upsert_item(args.langfuse_host, auth_header, dataset_name, item)
            upserted += 1
            log(f"  Upserted [{item['id']}]")
        except requests.RequestException as e:
            log(f"  Failed to upsert [{item['id']}]: {e}", "ERROR")
            failed += 1

    # ── Fetch existing items AFTER upserts (fresh snapshot) ──────────────
    # Fetching after upserting means this snapshot includes our own writes.
    # If a concurrent sync also upserted, we see their items too and won't
    # archive them (they're in Langfuse but not in our YAML — but we can't
    # distinguish them from items we need to archive, so the last sync wins).
    log(f"Fetching existing items for dataset '{dataset_name}'...")
    existing = fetch_existing_items(args.langfuse_host, auth_header, dataset_name)
    existing_api_ids = set(existing.keys())
    log(f"Found {len(existing)} items in Langfuse after upsert")

    # ── Determine archive candidates from fresh snapshot ────────────────
    to_archive = existing_api_ids - yaml_api_ids  # in Langfuse but not in yaml

    # Filter out items that are already archived (no need to re-archive)
    to_archive_active = {
        api_id for api_id in to_archive
        if existing.get(api_id, {}).get("status", "ACTIVE").upper() == "ACTIVE"
    }

    if not to_archive_active:
        log("Nothing to archive — dataset is in sync.", "INFO")
        version_ts = get_dataset_version_timestamp()
        print(version_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        return

    log(f"Plan: archive {len(to_archive_active)} items (from post-upsert snapshot)")

    # ── Archive removed items ─────────────────────────────────────────────
    # Skip archiving if any upserts failed — archiving removed items while
    # new items failed to create could leave the dataset missing both.
    archived = 0
    if failed:
        log("Skipping archive step due to upsert failures — re-run after fixing errors.", "WARN")
    else:
        for api_id in sorted(to_archive_active):
            try:
                archive_item(args.langfuse_host, auth_header, dataset_name, api_id)
                archived += 1
                logical_id = strip_dataset_prefix(api_id, dataset_name)
                log(f"  Archived [{logical_id}]")
            except requests.RequestException as e:
                log(f"  Failed to archive [{strip_dataset_prefix(api_id, dataset_name)}]: {e}", "ERROR")
                failed += 1

    if failed:
        log(f"{failed} operation(s) failed", "WARN")

    # ── Capture version timestamp ─────────────────────────────────────────
    # Brief pause to let Langfuse process the version bump server-side.
    if upserted or archived:
        time.sleep(POST_SYNC_SETTLE_SECONDS)

    version_ts = get_dataset_version_timestamp()

    # ── Summary ───────────────────────────────────────────────────────────
    log("=== SYNC COMPLETE ===")
    log(f"Dataset:   {dataset_name}")
    log(f"Upserted:  {upserted}")
    log(f"Archived:  {archived}")
    log(f"Failed:    {failed}")
    log(f"Version:   {version_ts.isoformat()}")

    # Print the version timestamp as the final line for programmatic capture.
    # Only emit on full success — a partial sync timestamp could mislead
    # callers into running experiments against an incomplete dataset.
    if failed == 0:
        print(version_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    else:
        log("Version pin not emitted due to sync failures — fix errors and re-run.", "WARN")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
