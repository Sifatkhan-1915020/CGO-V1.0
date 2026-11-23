"""
CareGuide Multi-Format Document & Tabular Extraction CLI
Accepts PDF, JPG, PNG, TIFF, XLSX, XLS, and CSV files.
Supports up to 1,000-page PDFs, 1,000 images, multi-sheet workbooks, and corrupted file isolation.
"""

import os
import sys
import argparse
import json
from careguide import DocumentOrchestrator, FileValidator


def parse_args():
    parser = argparse.ArgumentParser(
        description="CareGuide High-Scale Document & Tabular Extraction Engine",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="One or more files, directories, or glob patterns to process."
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=None,
        help="Public URL of the baidu/Unlimited-OCR Colab server (e.g. https://xxx.trycloudflare.com)."
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default="extraction_report.json",
        help="Path to save the consolidated JSON output report (default: extraction_report.json)."
    )
    parser.add_argument(
        "--stream-jsonl",
        type=str,
        default="careguide_stream.jsonl",
        help="Path to append real-time streaming results (default: careguide_stream.jsonl)."
    )
    parser.add_argument(
        "--checkpoint-db",
        type=str,
        default="careguide_checkpoint.db",
        help="Path to SQLite checkpoint database for resumable runs (default: careguide_checkpoint.db)."
    )
    parser.add_argument(
        "--batch-id",
        type=str,
        default=None,
        help="Batch run identifier (allows resuming interrupted batches)."
    )
    parser.add_argument(
        "--export-dir",
        type=str,
        default=None,
        help="Directory to save individual Markdown (.md) and CSV (.csv) exports for each document."
    )
    parser.add_argument(
        "--gemini-api-key",
        type=str,
        default=os.environ.get("GEMINI_API_KEY", None),
        help="Google Gemini API key for fallback OCR on <85% confidence and Bangla text."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Number of pages per batch to send to the GPU server (default: 20)."
    )
    return parser.parse_args()


def export_extracted_files(summary, export_dir: str):
    """Export pages to markdown and sheets to CSV files."""
    os.makedirs(export_dir, exist_ok=True)
    for doc in summary.documents:
        base_name = os.path.splitext(doc.filename)[0]

        # Export text / markdown pages
        if doc.pages:
            md_path = os.path.join(export_dir, f"{base_name}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"# Document: {doc.filename}\n\n")
                f.write(f"- Status: {doc.status.value}\n")
                f.write(f"- Pages: {len(doc.pages)}\n\n")
                for p in doc.pages:
                    if p.text and p.text.strip().startswith("=== this image"):
                        f.write(p.text.strip() + "\n\n")
                    else:
                        f.write(f"## Page {p.index} ({p.type.value})\n\n")
                        if p.text:
                            f.write(p.text + "\n\n")
                    if p.error:
                        f.write(f"> **Warning / Error**: {p.error}\n\n")
            print(f"  [Exported MD]  {md_path}")

        # Export spreadsheet sheets to CSV
        if doc.sheets:
            for s in doc.sheets:
                csv_path = os.path.join(export_dir, f"{base_name}_{s.name}.csv")
                with open(csv_path, "w", encoding="utf-8", newline="") as f:
                    import csv
                    if s.columns:
                        writer = csv.DictWriter(f, fieldnames=s.columns)
                        writer.writeheader()
                        writer.writerows(s.data)
                print(f"  [Exported CSV] {csv_path}")


def main():
    args = parse_args()

    print("=" * 70)
    print(" CareGuide Multi-Format Ingestion & Extraction Engine")
    print("=" * 70)
    if args.api_url:
        print(f"Connecting to Cloud Unlimited-OCR: {args.api_url}")
    else:
        print("Running in Local/Standby Mode (Digital PDFs & Spreadsheets fully extracted).")
        print("To enable full GPU Unlimited-OCR, pass --api-url <Colab_URL>")
    print("-" * 70)

    orchestrator = DocumentOrchestrator(
        api_url=args.api_url,
        gemini_api_key=args.gemini_api_key,
        checkpoint_db=args.checkpoint_db,
        jsonl_output=args.stream_jsonl,
        batch_id=args.batch_id,
        batch_size=args.batch_size
    )

    # Health check if API is configured
    if args.api_url:
        health = orchestrator.ocr_client.health_check()
        if health.get("status") == "healthy":
            print(f"Colab GPU Status: {health.get('gpu')} | Model: {health.get('model')}")
        else:
            print(f"Warning: Health check failed on {args.api_url}: {health}")

    summary = orchestrator.process_batch(args.inputs, show_progress=True)

    # Write consolidated report
    summary_dict = summary.model_dump()
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(summary_dict, f, indent=2)

    # Optional file export
    if args.export_dir:
        print("\nExporting individual document files:")
        export_extracted_files(summary, args.export_dir)

    print("\n" + "=" * 70)
    print(" BATCH EXECUTION SUMMARY")
    print("=" * 70)
    print(f"  Total Files Processed  : {summary.total_files}")
    print(f"  Successful Documents   : {summary.successful_files}")
    print(f"  Corrupted/Failed Files : {summary.corrupted_or_failed_files}")
    print(f"  Total Pages Extracted  : {summary.total_pages}")
    print(f"  Total Sheets Extracted : {summary.total_sheets}")
    print(f"  Total Duration (sec)   : {summary.total_duration_sec}s")
    print(f"  Throughput (pages/sec) : {summary.average_throughput_pages_per_sec} p/s")
    print(f"  JSON Report Saved To   : {os.path.abspath(args.output_json)}")
    print(f"  Streaming JSONL Saved  : {os.path.abspath(args.stream_jsonl)}")
    print(f"  Checkpoint Ledger DB   : {os.path.abspath(args.checkpoint_db)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
