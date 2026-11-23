#!/usr/bin/env python3
"""
CareGuide API Key Generator & Management CLI.

Usage Examples:
  # Generate a live API key for a client/app:
  python create_key.py customer01

  # Generate key with custom rate limit and scopes:
  python create_key.py partner-app --rpm 120 --limit 50000 --days 180 --scopes extract,invoices

  # Generate a test/sandbox key:
  python create_key.py dev-sandbox --test

  # List existing API keys:
  python create_key.py --list

  # Revoke a key:
  python create_key.py --revoke key_12345678abcdef

  # Inspect key info and usage:
  python create_key.py --info key_12345678abcdef
"""

import sys
import os
import argparse
from typing import List, Optional

# Ensure careguide package is importable
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from careguide.api_keys import (
    generate_api_key,
    list_api_keys,
    revoke_api_key,
    get_api_key_by_id,
    get_api_key_usage_stats,
    init_api_keys_db,
)


def print_banner():
    banner = """
========================================================================
             🛡️  CareGuide API Key Management Engine  🛡️
========================================================================
"""
    print(banner)


def handle_generate(args):
    name = args.name.strip()
    if not name:
        print("❌ Error: CUSTOMER_ID / APP_NAME cannot be empty.", file=sys.stderr)
        sys.exit(1)

    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()] if args.scopes else ["extract", "invoices", "health"]

    key_record = generate_api_key(
        name=name,
        scopes=scopes,
        rate_limit_rpm=args.rpm,
        total_limit=args.limit,
        expires_in_days=args.days if args.days > 0 else None,
        is_test=args.test,
        created_by="CLI_ADMIN"
    )

    print("\n✅ New CareGuide API Key Created Successfully!\n")
    print(f"  • Client / App Name : {key_record['name']}")
    print(f"  • Key ID            : {key_record['key_id']}")
    print(f"  • Key Type          : {'🧪 Test (Sandbox)' if args.test else '🚀 Production (Live)'}")
    print(f"  • Masked Display    : {key_record['masked_key']}")
    print(f"  • Scopes Granted    : {', '.join(key_record['scopes'])}")
    print(f"  • Rate Limit (RPM)  : {key_record['rate_limit_rpm']} req/min")
    quota_str = "Unlimited" if key_record["total_limit"] < 0 else f"{key_record['total_limit']} requests"
    print(f"  • Total Quota       : {quota_str}")
    print(f"  • Expiration        : {key_record['expires_at'] or 'Never (No expiration)'}")
    print(f"  • Created At (UTC)  : {key_record['created_at']}")
    print("\n" + "=" * 72)
    print("  🔑  RAW SECRET API KEY (Shown ONCE only — store in secure vault):")
    print(f"  \033[1;32m{key_record['api_key']}\033[0m")
    print("=" * 72 + "\n")

    # Provide curl usage example
    api_key = key_record["api_key"]
    print("💡 Example Usage (cURL):")
    print(f"""
  # 1. Health Check:
  curl -s -H "X-API-Key: {api_key}" http://localhost:8000/v1/health

  # 2. Document & Tabular Extraction:
  curl -X POST "http://localhost:8000/v1/extract" \\
       -H "X-API-Key: {api_key}" \\
       -F "files=@dummy_bangla_invoice.csv"

  # 3. Invoice Extraction & Multi-Field Validation:
  curl -X POST "http://localhost:8000/v1/invoices/extract" \\
       -H "Authorization: Bearer {api_key}" \\
       -F "files=@invoice.pdf"
""")


def handle_list(args):
    keys = list_api_keys()
    if not keys:
        print("\nℹ️  No API keys found in database. Create one using: python create_key.py <NAME>\n")
        return

    print(f"\n📋 Registered CareGuide API Keys ({len(keys)} total):\n")
    fmt = "{:<20} {:<18} {:<18} {:<10} {:<10} {:<10} {:<12}"
    print(fmt.format("KEY ID", "CLIENT NAME", "MASKED KEY", "RPM", "REQUESTS", "STATUS", "EXPIRES"))
    print("-" * 105)
    for k in keys:
        status = "ACTIVE" if k["is_active"] else "REVOKED"
        expires = (k["expires_at"][:10]) if k.get("expires_at") else "Never"
        print(fmt.format(
            k["key_id"],
            k["name"][:17],
            k["masked_key"],
            str(k["rate_limit_rpm"]),
            str(k["total_requests"]),
            status,
            expires
        ))
    print()


def handle_revoke(args):
    key_target = args.revoke.strip()
    success = revoke_api_key(key_target)
    if success:
        print(f"\n✅ API Key '{key_target}' has been successfully REVOKED.")
    else:
        print(f"\n❌ Failed to revoke: Key '{key_target}' not found in database.", file=sys.stderr)
        sys.exit(1)


def handle_info(args):
    key_id = args.info.strip()
    key_meta = get_api_key_by_id(key_id)
    if not key_meta:
        print(f"\n❌ API Key '{key_id}' not found.", file=sys.stderr)
        sys.exit(1)

    usage = get_api_key_usage_stats(key_id)

    print(f"\n🔍 API Key Details: {key_id}")
    print(f"  • Name            : {key_meta['name']}")
    print(f"  • Masked Key      : {key_meta['masked_key']}")
    print(f"  • Status          : {'ACTIVE' if key_meta['is_active'] else 'REVOKED'}")
    print(f"  • Scopes          : {', '.join(key_meta['scopes'])}")
    print(f"  • RPM Limit       : {key_meta['rate_limit_rpm']}")
    print(f"  • Total Quota     : {'Unlimited' if key_meta['total_limit'] < 0 else key_meta['total_limit']}")
    print(f"  • Total Requests  : {key_meta['total_requests']}")
    print(f"  • Last Used At    : {key_meta['last_used_at'] or 'Never'}")
    print(f"  • Avg Latency     : {usage.get('avg_latency_ms', 0)} ms")
    print(f"  • Errors Logged   : {usage.get('error_count', 0)}")

    recent = usage.get("recent_requests", [])
    if recent:
        print(f"\n  Recent Invocations ({len(recent)}):")
        for r in recent[:10]:
            print(f"    - {r['timestamp']} | {r['method']} {r['endpoint']} | Status {r['status_code']} | {r['latency_ms']}ms | IP {r.get('client_ip', 'unknown')}")
    print()


def main():
    init_api_keys_db()
    parser = argparse.ArgumentParser(
        description="CareGuide API Key Generator & Management CLI",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument("name", nargs="?", default=None, help="Customer / Application name (e.g. customer01, mobile-app)")
    parser.add_argument("--rpm", type=int, default=60, help="Rate limit in requests per minute (default: 60)")
    parser.add_argument("--limit", type=int, default=-1, help="Total lifetime requests allowed (-1 = unlimited, default: -1)")
    parser.add_argument("--days", type=int, default=90, help="Key validity in days (0 = never expires, default: 90)")
    parser.add_argument("--scopes", type=str, default="extract,invoices,health", help="Comma-separated scopes (default: extract,invoices,health)")
    parser.add_argument("--test", action="store_true", help="Generate sandbox test key (cg_test_...)")
    parser.add_argument("--list", action="store_true", help="List all registered API keys")
    parser.add_argument("--revoke", type=str, default=None, help="Revoke API key by key_id or key_hash")
    parser.add_argument("--info", type=str, default=None, help="Display usage statistics for an API key ID")

    args = parser.parse_args()

    print_banner()

    if args.list:
        handle_list(args)
    elif args.revoke:
        handle_revoke(args)
    elif args.info:
        handle_info(args)
    elif args.name:
        handle_generate(args)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
