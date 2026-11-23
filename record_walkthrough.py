#!/usr/bin/env python3
"""
Automated Screen Recording Engine for CareGuide Workspace & Swagger API.
Uses Playwright and headless Google Chrome to record a complete end-to-end
walkthrough of the CareGuide application and exports a 720p HD MP4 video.
"""

import os
import sys
import time
import shutil
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright
import imageio_ffmpeg

PROJECT_ROOT = Path(__file__).resolve().parent
TEMP_VIDEO_DIR = PROJECT_ROOT / "temp_recording"
OUTPUT_DIR = PROJECT_ROOT / "demo_output"
OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_VIDEO_DIR.mkdir(exist_ok=True)

CHROME_PATH = "/usr/bin/google-chrome"
TARGET_URL = "http://localhost:3001"
SAMPLE_FILE = PROJECT_ROOT / "dummy_bangla_invoice.csv"


def smooth_scroll(page, delta_y: int, steps: int = 15, delay: float = 0.03):
    step_y = delta_y / steps
    for _ in range(steps):
        page.evaluate(f"window.scrollBy(0, {step_y})")
        time.sleep(delay)


def run_recording():
    print("🎬 Starting CareGuide Automated Walkthrough Video Recording...")

    # Clear temp video dir
    for f in TEMP_VIDEO_DIR.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME_PATH,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1280,720"
            ]
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(TEMP_VIDEO_DIR),
            record_video_size={"width": 1280, "height": 720}
        )

        page = context.new_page()

        # -------------------------------------------------------------
        # SCENE 1: Landing Page & Architectural Badges (0:00 - 0:10)
        # -------------------------------------------------------------
        print("  ▶ [Scene 1] Loading Landing Page & Hero Section...")
        page.goto(TARGET_URL, wait_until="networkidle")
        time.sleep(2.5)

        # Smooth scroll through hero and architecture badges
        smooth_scroll(page, 480, steps=20, delay=0.04)
        time.sleep(2.0)
        smooth_scroll(page, -480, steps=20, delay=0.04)
        time.sleep(1.5)

        # -------------------------------------------------------------
        # SCENE 2: Admin Sign-in to Workspace Console (0:10 - 0:18)
        # -------------------------------------------------------------
        print("  ▶ [Scene 2] Authenticating as Administrator into Workspace...")
        admin_btn = page.locator("button:has-text('Admin Access')").first
        if admin_btn.is_visible():
            admin_btn.click()
            time.sleep(1.5)
            enter_admin = page.locator("button:has-text('Administrator')").first
            if enter_admin.is_visible():
                enter_admin.click()
                time.sleep(2.0)

        # -------------------------------------------------------------
        # SCENE 3: Document Ingestion & Batch Extraction (0:18 - 0:42)
        # -------------------------------------------------------------
        print("  ▶ [Scene 3] Ingesting Document & Triggering Extraction...")
        if SAMPLE_FILE.exists():
            file_input = page.locator("#file-upload-input")
            file_input.set_input_files(str(SAMPLE_FILE))
            time.sleep(2.0)

            # Click Run Extraction Pipeline
            extract_btn = page.locator("button:has-text('Run Extraction Pipeline'), button:has-text('Start OCR Extraction')").first
            if extract_btn.is_visible():
                extract_btn.click()
                print("    - Extraction pipeline active. Streaming telemetry logs...")
                time.sleep(6.0)

        # Scroll down to inspect results explorer & metrics
        smooth_scroll(page, 450, steps=18, delay=0.04)
        time.sleep(2.5)

        # -------------------------------------------------------------
        # SCENE 4: Task 3 Financial Validation Shield (0:42 - 0:56)
        # -------------------------------------------------------------
        print("  ▶ [Scene 4] Inspecting Audited Invoice & Validation Shield...")
        # Check for Task 3 tab or audit button
        invoice_tab = page.locator("button:has-text('Task 3: Invoices'), button:has-text('Invoice Intelligence')").first
        if invoice_tab.is_visible():
            invoice_tab.click()
            time.sleep(2.0)

        smooth_scroll(page, 280, steps=12, delay=0.04)
        time.sleep(2.5)
        smooth_scroll(page, -280, steps=12, delay=0.04)
        time.sleep(1.0)

        # -------------------------------------------------------------
        # SCENE 5: Settings & In-App API Key Generation (0:56 - 1:12)
        # -------------------------------------------------------------
        print("  ▶ [Scene 5] Opening Settings & Generating Cryptographic API Key...")
        smooth_scroll(page, -500, steps=15, delay=0.03)
        time.sleep(1.0)

        settings_btn = page.locator("button[title='Configure GPU & Batch Settings']").first
        if settings_btn.is_visible():
            settings_btn.click()
            time.sleep(1.8)

            # Scroll to Developer API Portal card
            smooth_scroll(page, 420, steps=15, delay=0.03)
            time.sleep(1.5)

            key_input = page.locator("input[placeholder*='Client / App Name']").first
            if key_input.is_visible():
                key_input.fill("enterprise-production-key")
                time.sleep(1.2)

                gen_btn = page.locator("button:has-text('Generate API Key')").first
                if gen_btn.is_visible():
                    gen_btn.click()
                    time.sleep(2.5)

                copy_btn = page.locator("button:has-text('Copy Key')").first
                if copy_btn.is_visible():
                    copy_btn.click()
                    time.sleep(1.5)

            # Close settings modal
            close_btn = page.locator("button:has-text('Close Settings'), button[title='Close']").first
            if close_btn.is_visible():
                close_btn.click()
                time.sleep(1.0)

        # -------------------------------------------------------------
        # SCENE 6: Interactive Swagger UI Portal (/docs) (1:12 - 1:35)
        # -------------------------------------------------------------
        print("  ▶ [Scene 6] Navigating to Live Swagger UI Documentation (/docs)...")
        page.goto(f"{TARGET_URL}/docs/index.html", wait_until="networkidle")
        time.sleep(3.0)

        # Smooth scroll through API specification
        smooth_scroll(page, 320, steps=15, delay=0.04)
        time.sleep(1.8)

        # Expand /v1/health endpoint and test it
        health_block = page.locator(".opblock-summary-get:has-text('/v1/health')").first
        if health_block.is_visible():
            health_block.click()
            time.sleep(1.5)

            try_btn = page.locator(".try-out__btn").first
            if try_btn.is_visible():
                try_btn.click()
                time.sleep(1.0)
                execute_btn = page.locator(".execute").first
                if execute_btn.is_visible():
                    execute_btn.click()
                    time.sleep(2.5)

        # Smooth scroll to show response body
        smooth_scroll(page, 280, steps=12, delay=0.04)
        time.sleep(2.5)

        # Smooth scroll through /v1/extract and /v1/invoices/extract
        smooth_scroll(page, 550, steps=18, delay=0.04)
        time.sleep(2.5)

        # Smooth scroll down to API key endpoints
        smooth_scroll(page, 400, steps=15, delay=0.04)
        time.sleep(2.0)

        # Final smooth scroll to top of Swagger docs
        smooth_scroll(page, -1200, steps=25, delay=0.03)
        time.sleep(2.0)

        print("  ✓ Recording interactions complete. Finalizing video stream...")
        page.close()
        context.close()
        browser.close()

    # Find the recorded WebM file
    webm_files = list(TEMP_VIDEO_DIR.glob("*.webm"))
    if not webm_files:
        print("❌ Error: No video file was generated in temp directory.")
        sys.exit(1)

    latest_webm = max(webm_files, key=os.path.getctime)
    final_mp4 = OUTPUT_DIR / "careguide_demo_walkthrough.mp4"
    final_webm = OUTPUT_DIR / "careguide_demo_walkthrough.webm"

    print(f"\n🎥 Raw recording saved to: {latest_webm}")
    print(f"📦 Transcoding to high-definition MP4: {final_mp4} ...")

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i", str(latest_webm),
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        str(final_mp4)
    ]

    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Also copy webm directly
    shutil.copy2(latest_webm, final_webm)

    size_mb = os.path.getsize(final_mp4) / (1024 * 1024)
    print(f"\n🎉 DEMO VIDEO RECORDED SUCCESSFULLY!")
    print(f"  • MP4 Video Path  : {final_mp4}")
    print(f"  • WebM Video Path : {final_webm}")
    print(f"  • File Size       : {size_mb:.2f} MB")
    print(f"  • Resolution      : 1280x720 HD\n")


if __name__ == "__main__":
    run_recording()
