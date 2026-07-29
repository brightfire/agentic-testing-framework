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


# ── Helpers ──────────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def make_auth_header(public_key, secret_key):
    """Build the Basic auth header from Langfuse API keys."""
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def parse_eval_yaml(path):
    """Parse an eval.yaml file and return (dataset_name, items_list).

    items_list is a list of dicts with keys: id, input, expected_output.
    Exits with an error if the file is malformed or missing required fields.
    """
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        log(f"File not found: {path}", "ERROR")
        sys.exit(1)
    except yaml.YAMLError as e:
        log(f"YAML parse error in {path}: {e}", "ERROR")
        sys.exit(1)

    if not isinstance(data, dict):
        log(f"Expected a YAML mapping at top level, got {type(data).__name__}", "ERROR")
        sys.exit(1)

    dataset_name = data.get("dataset")
    if not dataset_name:
        log("Missing required field 'dataset' in eval.yaml", "ERROR")
        sys.exit(1)

    items = data.get("items", [])
    if not isinstance(items, list):
        log("'items' must be a list", "ERROR")
        sys.exit(1)

    parsed = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            log(f"Item {i} is not a mapping: {item}", "ERROR")
            sys.exit(1)
        item_id = item.get("id")
        if not item_id:
            log(f"Item {i} missing required 'id' field", "ERROR")
            sys.exit(1)
        if not item.get("input"):
            log(f"Item '{item_id}' missing required 'input' field", "ERROR")
            sys.exit(1)
        parsed.append({
            "id": str(item_id),
            "input": item["input"],
            "expected_output": item.get("expected_output", ""),
        })

    return dataset_name, parsed


def fetch_existing_items(host, auth_header, dataset_name):
    """Fetch all ACTIVE dataset items from Langfuse for the given dataset.

    Returns a dict mapping item id → item dict (with status, updatedAt, etc.).
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

    The custom `id` field triggers upsert-on-conflict behaviour.
    Returns the API response dict.
    """
    url = f"{host}{API_BASE}/dataset-items"
    payload = {
        "id": item["id"],
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

    POST /api/public/dataset-items with the same id and status=ARCHIVED
    archives the item. Archived items are excluded from get_dataset() results
    (SDK defaults to ACTIVE only).
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


def get_dataset_version_timestamp(host, auth_header, dataset_name):
    """Determine the dataset version timestamp after sync.

    After all upserts/archives settle, we fetch the dataset's items and find
    the most recent `updatedAt` timestamp. This server-side timestamp is the
    most accurate version pin — it reflects when Langfuse last processed a
    write for this dataset.

    Falls back to datetime.now(utc) if the API doesn't return timestamps.
    """
    url = f"{host}{API_BASE}/dataset-items"
    params = {"datasetName": dataset_name, "limit": 100}
    resp = requests.get(url, params=params, headers=auth_header, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data", [])

    latest = None
    for item in data:
        updated = item.get("updatedAt")
        if updated:
            try:
                ts = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if latest is None or ts > latest:
                    latest = ts
            except (ValueError, TypeError):
                continue

    if latest is None:
        # Fallback: use current time if no updatedAt fields are present
        latest = datetime.now(timezone.utc)
        log("Could not extract updatedAt from dataset items, using datetime.now()", "WARN")

    return latest


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
        help="Parse and show what would be synced without making API calls",
    )
    args = parser.parse_args()

    # ── Validate credentials ──────────────────────────────────────────────
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")

    if not public_key or not secret_key:
        log("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables are required.", "ERROR")
        sys.exit(1)

    auth_header = make_auth_header(public_key, secret_key)

    # ── Parse eval.yaml ───────────────────────────────────────────────────
    dataset_name, yaml_items = parse_eval_yaml(args.file)
    yaml_ids = {item["id"] for item in yaml_items}

    log(f"Parsed eval.yaml: dataset='{dataset_name}', {len(yaml_items)} items")

    if args.dry_run:
        log("=== DRY RUN ===")
        for item in yaml_items:
            preview = item["input"][:80].replace("\n", " ")
            log(f"  [{item['id']}] input: {preview}...")
        log(f"Would upsert {len(yaml_items)} items to dataset '{dataset_name}'")
        log("No API calls made.")
        return

    # ── Fetch existing items from Langfuse ────────────────────────────────
    log(f"Fetching existing items for dataset '{dataset_name}'...")
    existing = fetch_existing_items(args.langfuse_host, auth_header, dataset_name)
    existing_ids = set(existing.keys())
    log(f"Found {len(existing)} existing items in Langfuse")

    # ── Determine actions ─────────────────────────────────────────────────
    to_upsert = yaml_items  # all items from yaml get upserted
    to_archive = existing_ids - yaml_ids  # in Langfuse but not in yaml

    # Filter out items that are already archived (no need to re-archive)
    to_archive_active = {
        item_id for item_id in to_archive
        if existing.get(item_id, {}).get("status", "ACTIVE").upper() == "ACTIVE"
    }

    log(f"Plan: upsert {len(to_upsert)} items, archive {len(to_archive_active)} items")

    if not to_upsert and not to_archive_active:
        log("Nothing to do — dataset is already in sync.", "INFO")
        # Still return a version timestamp for consistency
        version_ts = get_dataset_version_timestamp(
            args.langfuse_host, auth_header, dataset_name,
        )
        print(version_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ").replace("+00:00", "Z"))
        return

    # ── Upsert items ──────────────────────────────────────────────────────
    upserted = 0
    failed = 0
    for item in to_upsert:
        try:
            upsert_item(args.langfuse_host, auth_header, dataset_name, item)
            upserted += 1
            log(f"  Upserted [{item['id']}]")
        except requests.RequestException as e:
            log(f"  Failed to upsert [{item['id']}]: {e}", "ERROR")
            failed += 1

    # ── Archive removed items ─────────────────────────────────────────────
    archived = 0
    for item_id in sorted(to_archive_active):
        try:
            archive_item(args.langfuse_host, auth_header, dataset_name, item_id)
            archived += 1
            log(f"  Archived [{item_id}]")
        except requests.RequestException as e:
            log(f"  Failed to archive [{item_id}]: {e}", "ERROR")
            failed += 1

    if failed:
        log(f"{failed} operation(s) failed", "WARN")

    # ── Capture version timestamp ─────────────────────────────────────────
    # Brief pause to let Langfuse process the version bump server-side.
    if to_upsert or to_archive_active:
        time.sleep(POST_SYNC_SETTLE_SECONDS)

    version_ts = get_dataset_version_timestamp(
        args.langfuse_host, auth_header, dataset_name,
    )

    # ── Summary ───────────────────────────────────────────────────────────
    log("=== SYNC COMPLETE ===")
    log(f"Dataset:   {dataset_name}")
    log(f"Upserted:  {upserted}")
    log(f"Archived:  {archived}")
    log(f"Failed:    {failed}")
    log(f"Version:   {version_ts.isoformat()}")

    # Print the version timestamp as the final line for programmatic capture
    # Format: ISO-8601 UTC (e.g. 2026-07-29T15:51:00.000000Z)
    print(version_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ").replace("+00:00", "Z"))

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
