#!/usr/bin/env python3
"""
Dataset Sync Script — DEV-543

Reads an eval.yaml file (per DEV-321 schema) and syncs its items to a Langfuse
dataset. Creates the dataset if it doesn't exist, upserts items by id,
archives items no longer in the file. Optionally writes a per-item
manifest file (with timestamps) that can be passed to the eval harness
to pin the exact dataset state for experiment runs.

Usage:
    python src/dataset_sync.py --file skills/linear-create/eval.yaml
    python src/dataset_sync.py --file eval.yaml --dry-run
    python src/dataset_sync.py --file eval.yaml --langfuse-host http://localhost:3000

Credentials:
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables.
    Same env config as eval_harness.py and eval_report.py.

Schema validation is handled by src/schema.py — see that file's docstring
for the full eval dataset schema documentation.

Output:
    Logs sync progress to stdout. With --output-manifest <path>, writes a
    JSON manifest containing per-item timestamps (from Langfuse server
    responses) that can be passed to the eval harness for version pinning.
"""

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
import yaml
from pydantic import ValidationError

from schema import EvalFile, ExpectedOutput, ID_SEPARATOR


# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_LANGFUSE_HOST = "http://localhost:3000"
API_BASE = "/api/public"


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
    """Parse and validate an eval.yaml file.

    Returns (dataset_name, description, items_list) where items_list is a
    list of dicts with keys: id, input, expected_output.
    Exits with an error if the file is malformed or fails schema validation.
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

    try:
        validated = EvalFile.model_validate(data)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            log(f"Validation error at '{loc}': {err['msg']}", "ERROR")
        sys.exit(1)

    # Convert to plain dicts for the sync logic
    items = [
        {
            "id": item.id,
            "input": item.input,
            # Serialize structured expected_output back to a plain dict for the API
            "expected_output": item.expected_output.model_dump() if isinstance(item.expected_output, ExpectedOutput) else item.expected_output,
        }
        for item in validated.items
    ]

    return validated.dataset, validated.description, items


def ensure_dataset_exists(host, auth_header, dataset_name, description=None):
    """Create the dataset in Langfuse if it doesn't already exist.

    POST /api/public/datasets is idempotent — if the dataset name already
    exists, it returns the existing dataset. Safe to call before every sync.
    """
    url = f"{host}{API_BASE}/datasets"
    payload = {"name": dataset_name}
    if description:
        payload["description"] = description
    resp = requests.post(url, json=payload, headers=auth_header, timeout=15)
    resp.raise_for_status()
    return resp.json()


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
    return resp


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
    return resp


def get_server_version_timestamp(date_header, response_body=None):
    """Derive the dataset version timestamp from the Langfuse server.

    Prefers the ``updatedAt`` field from the last write response body —
    this is the server's own record of when the item was last modified,
    with millisecond precision. Falls back to the HTTP ``Date`` header
    (second precision) if the body is unavailable, then to the local UTC
    clock as a last resort.
    """
    if response_body and isinstance(response_body, dict):
        updated = response_body.get("updatedAt")
        if updated:
            try:
                dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                return dt.astimezone(timezone.utc)
            except (ValueError, TypeError):
                log(f"Could not parse updatedAt '{updated}' from response body", "WARN")
    if date_header:
        try:
            dt = parsedate_to_datetime(date_header)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            log(f"Could not parse Date header '{date_header}', falling back to local clock", "WARN")
    return datetime.now(timezone.utc)


def write_manifest(output_path, dataset_name, manifest_items, sync_completed_at):
    """Write the sync manifest JSON file if --output-manifest was provided.

    Returns True on success or if no output path was given, False on write
    failure. Callers should check the return value when --output-manifest was
    explicitly requested and exit non-zero on failure.
    """
    if not output_path:
        return True
    manifest = {
        "dataset": dataset_name,
        "synced_at": sync_completed_at.isoformat(),
        "items": manifest_items,
    }
    try:
        with open(output_path, "w") as f:
            json.dump(manifest, f, indent=2)
            f.write("\n")
        print(output_path, flush=True)
        return True
    except OSError as e:
        log(f"Failed to write manifest to {output_path}: {e}", "ERROR")
        return False


def try_dry_run_archive_preview(langfuse_host, auth_header, dataset_name, yaml_ids):
    """Fetch existing items from Langfuse to preview archive candidates.

    Dry-run never makes write calls but does read-only GETs to show what
    would be archived.
    """
    try:
        existing = fetch_existing_items(langfuse_host, auth_header, dataset_name)
        existing_api_ids = set(existing.keys())
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
        help="Parse and show sync plan without making write calls",
    )
    parser.add_argument(
        "--output-manifest", default=None, metavar="PATH",
        help="Write a JSON manifest file at this path after sync (per-item timestamps)",
    )
    parser.add_argument(
        "--items", default=None,
        help="Comma-separated list of item IDs to sync (from eval.yaml). Only these items are upserted and included in the manifest. If omitted, all items in eval.yaml are synced.",
    )
    args = parser.parse_args()

    # ── Parse and validate eval.yaml (no credentials needed) ──────────────
    dataset_name, description, yaml_items = parse_eval_yaml(args.file)

    # ── Filter to requested items if --items is specified ────────────────
    if args.items:
        requested_ids = set(id.strip() for id in args.items.split(",") if id.strip())
        yaml_items = [item for item in yaml_items if item["id"] in requested_ids]
        if not yaml_items:
            log(f"No items in eval.yaml match the requested IDs: {sorted(requested_ids)}", "ERROR")
            sys.exit(1)
        log(f"Filtered to {len(yaml_items)} requested item(s): {[item['id'] for item in yaml_items]}")

    yaml_api_ids = {make_api_id(dataset_name, item["id"]) for item in yaml_items}

    log(f"Parsed eval.yaml: dataset='{dataset_name}', {len(yaml_items)} items")

    # ── Validate credentials ────────────────────────────────────────────
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")

    if not public_key or not secret_key:
        log("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables are required.", "ERROR")
        sys.exit(1)

    auth_header = make_auth_header(public_key, secret_key)

    if args.dry_run:
        log("=== DRY RUN ===")
        for item in yaml_items:
            preview = item["input"][:80].replace("\n", " ")
            log(f"  [upsert] {item['id']}: {preview}...")
        log(f"Would upsert {len(yaml_items)} items to dataset '{dataset_name}'")

        try_dry_run_archive_preview(args.langfuse_host, auth_header, dataset_name, {item["id"] for item in yaml_items})

        log("No write calls made.")
        return

    # ── Ensure dataset exists before upserting items ─────────────────────
    log(f"Ensuring dataset '{dataset_name}' exists...")
    try:
        ensure_dataset_exists(args.langfuse_host, auth_header, dataset_name, description)
    except requests.RequestException as e:
        log(f"Failed to create/verify dataset '{dataset_name}': {e}", "ERROR")
        sys.exit(1)

    # ── Upsert items first (no dependency on existing state) ─────────────
    upserted = 0
    failed = 0
    manifest_items = []
    sync_start = datetime.now(timezone.utc)
    for item in yaml_items:
        api_id = make_api_id(dataset_name, item["id"])
        try:
            resp = upsert_item(args.langfuse_host, auth_header, dataset_name, item)
            date_str = resp.headers.get("Date")
            try:
                resp_body = resp.json()
            except (ValueError, TypeError):
                resp_body = None
            item_ts = get_server_version_timestamp(date_str, resp_body)
            upserted += 1
            log(f"  Upserted [{item['id']}]")
            manifest_items.append({
                "id": api_id,
                "timestamp": item_ts.isoformat(),
            })
        except requests.RequestException as e:
            log(f"  Failed to upsert [{item['id']}]: {e}", "ERROR")
            failed += 1

    # ── Fetch existing items AFTER upserts (fresh snapshot) ──────────────
    log(f"Fetching existing items for dataset '{dataset_name}'...")
    existing = fetch_existing_items(args.langfuse_host, auth_header, dataset_name)
    existing_api_ids = set(existing.keys())
    log(f"Found {len(existing)} items in Langfuse after upsert")

    # ── Determine archive candidates from fresh snapshot ────────────────
    # Skip archiving when --items is used — partial syncs should not remove other DSIs
    if args.items:
        to_archive_active = set()
        log("Skipping archive step (--items filter active)")
    else:
        to_archive = existing_api_ids - yaml_api_ids
        to_archive_active = {
            api_id for api_id in to_archive
            if existing.get(api_id, {}).get("status", "ACTIVE").upper() == "ACTIVE"
        }

    if not to_archive_active:
        if failed:
            log(f"{failed} upsert(s) failed — fix errors and re-run.", "WARN")
            sync_completed_at = datetime.now(timezone.utc)
            write_manifest(args.output_manifest, dataset_name, manifest_items, sync_completed_at)
            sys.exit(1)
        log("Nothing to archive — dataset is in sync.", "INFO")
        sync_completed_at = datetime.now(timezone.utc)
        if not write_manifest(args.output_manifest, dataset_name, manifest_items, sync_completed_at):
            sys.exit(1)
        return

    log(f"Plan: archive {len(to_archive_active)} items (from post-upsert snapshot)")

    # ── Archive removed items ─────────────────────────────────────────────
    archived = 0
    if failed:
        log("Skipping archive step due to upsert failures — re-run after fixing errors.", "WARN")
    else:
        for api_id in sorted(to_archive_active):
            try:
                resp = archive_item(args.langfuse_host, auth_header, dataset_name, api_id)
                date_str = resp.headers.get("Date")
                try:
                    resp_body = resp.json()
                except (ValueError, TypeError):
                    resp_body = None
                item_ts = get_server_version_timestamp(date_str, resp_body)
                archived += 1
                logical_id = strip_dataset_prefix(api_id, dataset_name)
                log(f"  Archived [{logical_id}]")
            except requests.RequestException as e:
                log(f"  Failed to archive [{strip_dataset_prefix(api_id, dataset_name)}]: {e}", "ERROR")
                failed += 1

    if failed:
        log(f"{failed} operation(s) failed", "WARN")

    # ── Summary ───────────────────────────────────────────────────────────
    log("=== SYNC COMPLETE ===")
    log(f"Dataset:   {dataset_name}")
    log(f"Upserted:  {upserted}")
    log(f"Archived:  {archived}")
    log(f"Failed:    {failed}")

    sync_completed_at = datetime.now(timezone.utc)
    if not write_manifest(args.output_manifest, dataset_name, manifest_items, sync_completed_at):
        sys.exit(1)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
