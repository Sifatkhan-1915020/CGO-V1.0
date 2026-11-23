"""
CareGuide Multi-Format Document & Tabular Extraction Web Application
Built with Streamlit.
Supports PDF, JPG, PNG, TIFF, XLSX, XLS, and CSV uploads with live OCR/Tabular extraction,
real-time progress tracking, and interactive streaming execution logs.
"""

import os
import io
import time
import json
import zipfile
import tempfile
from datetime import datetime
import pandas as pd
import streamlit as st

from careguide import DocumentOrchestrator, FileValidator
from careguide.schema import ProcessingStatus, ExtractionType, DocumentType

# Page configuration
st.set_page_config(
    page_title="CareGuide Document Extraction Engine",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        font-size: 1.05rem;
        color: #64748B;
        margin-bottom: 1.5rem;
    }
    .status-card {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 10px 14px;
        margin-bottom: 10px;
    }
    .status-card-label {
        font-size: 0.8rem;
        color: #64748B;
        font-weight: 600;
        text-transform: uppercase;
    }
    .status-card-value {
        font-size: 1.15rem;
        font-weight: 700;
        color: #0F172A;
    }
    .log-container {
        background-color: #0F172A;
        color: #38BDF8;
        font-family: 'Consolas', 'Courier New', monospace;
        padding: 14px;
        border-radius: 8px;
        height: 260px;
        overflow-y: auto;
        font-size: 0.88rem;
        line-height: 1.45;
        border: 1px solid #1E293B;
        box-shadow: inset 0 2px 4px rgba(0,0,0,0.4);
    }
</style>
""", unsafe_allow_html=True)


# Sidebar Configuration
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/documents.png", width=64)
    st.title("Settings")
    st.markdown("Configure extraction backends and endpoints.")

    default_api = "https://observed-blend-imagination-wed.trycloudflare.com"
    api_url = st.text_input(
        "Unlimited-OCR API URL",
        value=default_api,
        help="Public URL of the Kaggle/Colab GPU server (leave blank to run local standby mode)."
    )

    gemini_api_key = st.text_input(
        "Gemini API Key (Bangla / <85% Fallback)",
        value=os.environ.get("GEMINI_API_KEY", ""),
        type="password",
        help="API Key for Gemini Vision AI fallback when confidence < 85% or for complex Bangla text."
    )

    batch_size = st.slider(
        "Batch Size (Pages per pass)",
        min_value=5,
        max_value=50,
        value=20,
        step=5,
        help="Number of pages/images dispatched in a single batch to the GPU server (default: 20)."
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔌 Ping OCR"):
            from careguide.ocr_client import OCRClient
            client = OCRClient(api_url=api_url, gemini_api_key=gemini_api_key)
            with st.spinner("Pinging OCR..."):
                health = client.health_check()
                if health.get("status") == "healthy":
                    st.success(f"Connected! GPU: {health.get('gpu', 'N/A')}")
                else:
                    st.warning("OCR standby mode.")
    with col2:
        if st.button("✨ Test Gemini"):
            if not gemini_api_key.strip():
                st.error("Enter Gemini Key first.")
            else:
                from careguide.gemini_fallback import GeminiOCRClient
                gemini_cli = GeminiOCRClient(api_key=gemini_api_key.strip())
                ok, msg = gemini_cli.verify_key()
                if ok:
                    st.success(f"Active: {msg}")
                else:
                    st.error(f"Failed: {msg}")

    st.markdown("---")
    st.subheader("Engine Features")
    st.markdown("""
    - **PDF Engine**: PyMuPDF Fast-Path (< 15ms)
    - **OCR Engine**: baidu/Unlimited-OCR (GPU)
    - **Bangla Fallback**: Gemini Vision AI (<85% conf)
    - **Image Epochs**: 20 images per batch pass
    - **Image CV**: OpenCV Deskew, Denoise, Binarize
    - **Tabular**: openpyxl streaming, xlrd, CSV sniffer
    - **Resilience**: Checkpointed SQLite + JSONL
    """)

    if "results" in st.session_state:
        if st.button("🔄 Clear Results & Reset"):
            del st.session_state["results"]
            if "logs" in st.session_state:
                del st.session_state["logs"]
            st.rerun()


# Main Header
st.markdown('<div class="main-title">CareGuide Document & Tabular Extraction</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Accepts multiple PDF, JPG, PNG, TIFF, XLSX, XLS, and CSV files. Extracts digital text, runs GPU OCR, and parses structured workbooks.</div>', unsafe_allow_html=True)

# File Uploader
uploaded_files = st.file_uploader(
    "Upload one or more documents (Drag and Drop supported):",
    type=["pdf", "jpg", "jpeg", "png", "tif", "tiff", "xlsx", "xls", "csv"],
    accept_multiple_files=True,
    help="Supports up to 1,000-page PDFs, batches of images, and multi-sheet spreadsheets."
)

if uploaded_files:
    st.write(f"📁 **{len(uploaded_files)} file(s) selected for processing.**")

    if st.button("🚀 Run Extraction Pipeline", type="primary", use_container_width=True):
        st.markdown("---")
        st.subheader("⚡ Live Extraction Status & Activity Log")

        # 1. Progress Bar & Metric Cards
        progress_bar = st.progress(0.0)

        stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
        with stat_col1:
            file_metric = st.empty()
        with stat_col2:
            item_metric = st.empty()
        with stat_col3:
            mode_metric = st.empty()
        with stat_col4:
            timer_metric = st.empty()

        file_metric.markdown("""
            <div class="status-card">
                <div class="status-card-label">Current Document</div>
                <div class="status-card-value">Initializing...</div>
            </div>
        """, unsafe_allow_html=True)

        item_metric.markdown("""
            <div class="status-card">
                <div class="status-card-label">Current Page / Sheet</div>
                <div class="status-card-value">Pending</div>
            </div>
        """, unsafe_allow_html=True)

        mode_metric.markdown("""
            <div class="status-card">
                <div class="status-card-label">Active Engine</div>
                <div class="status-card-value">Triage / Queue</div>
            </div>
        """, unsafe_allow_html=True)

        timer_metric.markdown("""
            <div class="status-card">
                <div class="status-card-label">Elapsed Time</div>
                <div class="status-card-value">0.0s</div>
            </div>
        """, unsafe_allow_html=True)

        # 2. Live Terminal Log Console
        st.markdown("**Live Execution Log:**")
        log_box = st.empty()
        logs_list = []

        def append_log(msg: str, tag: str = "INFO"):
            t_str = datetime.now().strftime("%H:%M:%S")
            entry = f"[{t_str}] [{tag}] {msg}"
            logs_list.append(entry)
            # Display last 12 log lines in the log box
            formatted_logs = "\n".join(logs_list[-14:])
            log_box.code(formatted_logs, language="bash")

        append_log(f"Pipeline started. Enqueued {len(uploaded_files)} file(s).", tag="INIT")

        # Temporary work directory for uploaded files
        temp_dir = tempfile.mkdtemp(prefix="careguide_web_")
        saved_paths = []

        for uploaded_file in uploaded_files:
            file_path = os.path.join(temp_dir, uploaded_file.name)
            with open(file_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            saved_paths.append(file_path)

        append_log("Temporary storage verified. Initializing DocumentOrchestrator...", tag="PREP")

        # Initialize orchestrator
        db_path = os.path.join(temp_dir, "web_checkpoint.db")
        jsonl_path = os.path.join(temp_dir, "web_stream.jsonl")
        orchestrator = DocumentOrchestrator(
            api_url=api_url if api_url.strip() else None,
            gemini_api_key=gemini_api_key.strip() if gemini_api_key.strip() else None,
            checkpoint_db=db_path,
            jsonl_output=jsonl_path,
            batch_size=batch_size
        )

        total_files = len(saved_paths)
        documents_results = []
        batch_start = time.time()

        # Callback for page/sheet granular updates
        def callback(filename, msg, item=None):
            elapsed_now = round(time.time() - batch_start, 1)
            timer_metric.markdown(f"""
                <div class="status-card">
                    <div class="status-card-label">Elapsed Time</div>
                    <div class="status-card-value">{elapsed_now}s</div>
                </div>
            """, unsafe_allow_html=True)

            engine_mode = "Triage"
            tag = "STEP"
            if "DIGITAL_TEXT" in msg:
                engine_mode = "Vector Text (<15ms)"
                tag = "DIGITAL"
            elif "Gemini" in msg or "Bangla" in msg:
                engine_mode = "Gemini Vision AI (Bangla)"
                tag = "GEMINI"
            elif "UNLIMITED_OCR" in msg or "OCR" in msg:
                engine_mode = "Unlimited-OCR (GPU)"
                tag = "OCR"
            elif "Sheet" in msg:
                engine_mode = "Tabular Parser"
                tag = "TABULAR"
            elif "Validation" in msg:
                engine_mode = "Format Validator"
                tag = "SHIELD"
            elif "Epoch" in msg or "images" in msg:
                engine_mode = f"Epoch Batch ({batch_size} imgs/pass)"
                tag = "EPOCH"

            item_metric.markdown(f"""
                <div class="status-card">
                    <div class="status-card-label">Current Page / Sheet / Epoch</div>
                    <div class="status-card-value">{msg.split(' - ')[0][:24]}</div>
                </div>
            """, unsafe_allow_html=True)

            mode_metric.markdown(f"""
                <div class="status-card">
                    <div class="status-card-label">Active Engine</div>
                    <div class="status-card-value">{engine_mode}</div>
                </div>
            """, unsafe_allow_html=True)

            append_log(f"{filename} -> {msg}", tag=tag)

        # Process all files with 20-image epoching and 20-page PDF batching
        documents_results = orchestrator.process_files(saved_paths, progress_callback=callback)
        progress_bar.progress(1.0)

            status_tag = "SUCCESS" if doc_res.status == ProcessingStatus.SUCCESS else "WARNING"
            append_log(f"Completed '{fname}' -> Status: {doc_res.status.value} (Items: {doc_res.total_pages_or_sheets}, Time: {doc_res.processing_time_ms}ms)", tag=status_tag)

        total_duration = round(time.time() - batch_start, 2)
        progress_bar.progress(1.0)
        append_log(f"All {total_files} documents finished in {total_duration}s.", tag="FINISH")

        timer_metric.markdown(f"""
            <div class="status-card">
                <div class="status-card-label">Total Duration</div>
                <div class="status-card-value">{total_duration}s</div>
            </div>
        """, unsafe_allow_html=True)

        mode_metric.markdown("""
            <div class="status-card">
                <div class="status-card-label">Pipeline Status</div>
                <div class="status-card-value" style="color: #10B981;">Complete</div>
            </div>
        """, unsafe_allow_html=True)

        # Calculate metrics
        total_pages = sum(len(d.pages) for d in documents_results)
        total_sheets = sum(len(d.sheets) for d in documents_results)
        successful = sum(1 for d in documents_results if d.status == ProcessingStatus.SUCCESS)
        corrupted_or_failed = sum(1 for d in documents_results if d.status in (ProcessingStatus.FAILED, ProcessingStatus.CORRUPTED))
        throughput = round(total_pages / total_duration, 2) if total_duration > 0 else float(total_pages)

        st.session_state["results"] = {
            "documents": documents_results,
            "total_files": total_files,
            "total_pages": total_pages,
            "total_sheets": total_sheets,
            "successful": successful,
            "failed": corrupted_or_failed,
            "duration": total_duration,
            "throughput": throughput
        }
        st.session_state["logs"] = logs_list


# Render Results
if "results" in st.session_state:
    res = st.session_state["results"]
    docs = res["documents"]

    st.markdown("---")
    st.subheader("📊 Extraction Summary")

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Total Documents", res["total_files"])
    with col2:
        st.metric("Total Pages Extracted", res["total_pages"])
    with col3:
        st.metric("Spreadsheet Sheets", res["total_sheets"])
    with col4:
        st.metric("Total Duration", f"{res['duration']}s")
    with col5:
        st.metric("Throughput", f"{res['throughput']} p/s")

    # Download Package
    st.markdown("### 📥 Download Results")
    d_col1, d_col2, d_col3 = st.columns(3)

    # 1. Full JSON Report
    report_dict = {
        "summary": {
            "total_files": res["total_files"],
            "total_pages": res["total_pages"],
            "total_sheets": res["total_sheets"],
            "successful": res["successful"],
            "failed": res["failed"],
            "duration_sec": res["duration"]
        },
        "documents": [d.model_dump() for d in docs]
    }
    json_bytes = json.dumps(report_dict, indent=2).encode("utf-8")

    with d_col1:
        st.download_button(
            label="📄 Download JSON Report",
            data=json_bytes,
            file_name="extraction_report.json",
            mime="application/json",
            use_container_width=True
        )

    # 2. ZIP Archive with .md and .csv files
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for doc in docs:
            bname = os.path.splitext(doc.filename)[0]
            # Write markdown pages
            if doc.pages:
                md_content = f"# {doc.filename}\n\nStatus: {doc.status.value}\nTotal Pages: {len(doc.pages)}\n\n"
                for p in doc.pages:
                    md_content += f"## Page {p.index} ({p.type.value})\n\n{p.text}\n\n"
                    if p.error:
                        md_content += f"> Error: {p.error}\n\n"
                zip_file.writestr(f"{bname}.md", md_content)
            # Write CSV sheets
            if doc.sheets:
                for s in doc.sheets:
                    if s.columns:
                        df_sheet = pd.DataFrame(s.data)
                        csv_data = df_sheet.to_csv(index=False)
                        zip_file.writestr(f"{bname}_{s.name}.csv", csv_data)

    zip_buffer.seek(0)
    with d_col2:
        st.download_button(
            label="📦 Download ZIP (Markdown + CSVs)",
            data=zip_buffer.getvalue(),
            file_name="extracted_documents.zip",
            mime="application/zip",
            use_container_width=True
        )

    # 3. Download Full Log File
    if "logs" in st.session_state:
        full_logs_txt = "\n".join(st.session_state["logs"])
        with d_col3:
            st.download_button(
                label="📜 Download Execution Log (.txt)",
                data=full_logs_txt,
                file_name="execution_log.txt",
                mime="text/plain",
                use_container_width=True
            )

    # Historical Execution Log Expander
    if "logs" in st.session_state:
        with st.expander("📜 View Full Execution Activity Log", expanded=False):
            st.code("\n".join(st.session_state["logs"]), language="bash")

    # Detailed Document Viewer
    st.markdown("---")
    st.subheader("📑 Document Inspection")

    for i, doc in enumerate(docs):
        status_color = "🟢" if doc.status == ProcessingStatus.SUCCESS else ("🟡" if doc.status == ProcessingStatus.PARTIAL_SUCCESS else "🔴")
        header_title = f"{status_color} {doc.filename} — {doc.file_type.value} ({doc.status.value}) [Time: {doc.processing_time_ms} ms]"

        with st.expander(header_title, expanded=(i == 0)):
            # Diagnostics & Errors
            if doc.error:
                st.error(f"**Document Warning / Error**: {doc.error}")

            # Pages (PDF / Image)
            if doc.pages:
                st.markdown(f"**Extracted Pages ({len(doc.pages)}):**")
                page_tabs = st.tabs([f"Page {p.index}" for p in doc.pages])
                for p_idx, page in enumerate(doc.pages):
                    with page_tabs[p_idx]:
                        info_cols = st.columns(4)
                        info_cols[0].write(f"**Type:** `{page.type.value}`")
                        info_cols[1].write(f"**Status:** `{page.status.value}`")
                        info_cols[2].write(f"**Confidence:** `{round(page.confidence, 2)}`")
                        info_cols[3].write(f"**Latency:** `{page.elapsed_ms} ms`")

                        if page.preprocessing_applied:
                            st.write(f"**Enhancements:** {', '.join([f'`{op}`' for op in page.preprocessing_applied])}")

                        if page.error:
                            st.warning(f"Page Warning: {page.error}")

                        if page.text:
                            st.markdown("##### Extracted Text / Markdown:")
                            st.markdown(page.text)
                            st.text_area("Raw Text (Select / Copy):", value=page.text, height=180, key=f"raw_{doc.doc_id}_{page.index}")

            # Sheets (Spreadsheets)
            if doc.sheets:
                st.markdown(f"**Extracted Sheets ({len(doc.sheets)}):**")
                sheet_tabs = st.tabs([f"Sheet: {s.name}" for s in doc.sheets])
                for s_idx, sheet in enumerate(doc.sheets):
                    with sheet_tabs[s_idx]:
                        s_cols = st.columns(3)
                        s_cols[0].write(f"**Rows:** `{sheet.row_count}`")
                        s_cols[1].write(f"**Columns:** `{sheet.col_count}`")
                        s_cols[2].write(f"**Status:** `{sheet.status.value}`")

                        if sheet.error:
                            st.error(f"Sheet Error: {sheet.error}")

                        if sheet.data:
                            df = pd.DataFrame(sheet.data)
                            st.dataframe(df, use_container_width=True)
                            
                            csv_buffer = io.StringIO()
                            df.to_csv(csv_buffer, index=False)
                            st.download_button(
                                label=f"⬇️ Export '{sheet.name}' as CSV",
                                data=csv_buffer.getvalue(),
                                file_name=f"{doc.filename}_{sheet.name}.csv",
                                mime="text/csv",
                                key=f"csv_btn_{doc.doc_id}_{s_idx}"
                            )
                        else:
                            st.info("Sheet is empty or has no data rows.")
