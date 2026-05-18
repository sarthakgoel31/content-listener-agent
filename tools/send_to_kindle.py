#!/usr/bin/env python3
"""Send documents to Kindle via Gmail SMTP with macOS Keychain auth.

Includes a file processing pipeline that cleans and converts documents
(PDF, DOCX, etc.) to Kindle-optimized HTML before sending, so the
reading experience on Kindle is excellent.
"""

import argparse
import mimetypes
import os
import re
import smtplib
import subprocess
import sys
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from shutil import which

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
KINDLE_EMAIL = os.environ.get("KINDLE_EMAIL", "")

if not GMAIL_ADDRESS or not KINDLE_EMAIL:
    raise ValueError(
        "GMAIL_ADDRESS and KINDLE_EMAIL environment variables must be set. "
        "Example: export GMAIL_ADDRESS='you@gmail.com' KINDLE_EMAIL='you@kindle.com'"
    )

KEYCHAIN_SERVICE = "kindle-agent-gmail"
KEYCHAIN_ACCOUNT = GMAIL_ADDRESS

KINDLE_SUPPORTED = {
    ".pdf", ".mobi", ".epub", ".doc", ".docx", ".txt", ".rtf",
    ".html", ".htm", ".png", ".jpg", ".jpeg", ".gif", ".bmp",
}

# Formats where we should extract text and convert to clean HTML
PROCESSABLE_FORMATS = {".pdf", ".docx", ".doc", ".rtf"}


# ---------------------------------------------------------------------------
# Keychain helpers
# ---------------------------------------------------------------------------

def get_app_password():
    """Retrieve Gmail App Password from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def store_app_password(password):
    """Store Gmail App Password in macOS Keychain."""
    subprocess.run(
        ["security", "delete-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
        capture_output=True,
    )
    result = subprocess.run(
        ["security", "add-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", password],
        capture_output=True, text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

def clean_extracted_text(text: str) -> str:
    """Clean text extracted from PDFs/docs for Kindle reading.

    Removes page numbers, common headers/footers, fixes hyphenated line
    breaks, and normalizes whitespace.
    """
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip standalone page numbers (e.g., "42", "- 12 -", "Page 5")
        if re.match(r'^-?\s*\d{1,4}\s*-?$', stripped):
            continue
        if re.match(r'^(page|pg\.?)\s*\d+\s*$', stripped, re.I):
            continue
        # Skip lines that are just repeated characters (decorative separators)
        if re.match(r'^[=\-_*~.]{5,}$', stripped):
            continue
        cleaned.append(line)

    text = "\n".join(cleaned)

    # Fix hyphenated line breaks (e.g., "docu-\nment" -> "document")
    text = re.sub(r'(\w)-\n\s*(\w)', r'\1\2', text)

    # Collapse 3+ blank lines into 2
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def detect_structure(text: str) -> list[dict]:
    """Detect headings, paragraphs, and lists from extracted text.

    Returns a list of dicts with 'type' (heading, paragraph, list_item)
    and 'content'.
    """
    blocks = re.split(r'\n{2,}', text)
    elements = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.split("\n")

        # Heading heuristic: short line, no ending period, possibly ALL CAPS
        # or starts with a number followed by a period/dot (like "1. Introduction")
        if len(lines) == 1 and len(block) < 120 and not block.endswith('.'):
            # ALL CAPS or Title Case with no period — likely a heading
            if block.isupper() or re.match(r'^\d+[\.\)]\s+', block):
                elements.append({"type": "heading", "content": block})
                continue
            # Short standalone line that looks like a title
            if len(block) < 60 and block[0].isupper():
                elements.append({"type": "heading", "content": block})
                continue

        # List items: lines starting with bullets or numbers
        list_items = []
        non_list = []
        for line in lines:
            line = line.strip()
            if re.match(r'^[\u2022\u2023\u25E6\u2043\-\*]\s+', line):
                list_items.append(re.sub(r'^[\u2022\u2023\u25E6\u2043\-\*]\s+', '', line))
            elif re.match(r'^\d+[\.\)]\s+', line) and len(line) < 200:
                list_items.append(re.sub(r'^\d+[\.\)]\s+', '', line))
            else:
                non_list.append(line)

        if list_items and not non_list:
            for item in list_items:
                elements.append({"type": "list_item", "content": item})
        else:
            # Regular paragraph — rejoin lines
            para = " ".join(l.strip() for l in lines)
            # Clean up extra spaces
            para = re.sub(r'\s{2,}', ' ', para)
            elements.append({"type": "paragraph", "content": para})

    return elements


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def text_to_html(text: str, title: str = "Document") -> str:
    """Convert plain text to clean, Kindle-friendly HTML with structure detection."""
    import html as html_mod

    elements = detect_structure(text)
    html_parts = []
    in_list = False

    for el in elements:
        content = html_mod.escape(el["content"])

        if el["type"] == "heading":
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h2>{content}</h2>")

        elif el["type"] == "list_item":
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{content}</li>")

        elif el["type"] == "paragraph":
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{content}</p>")

    if in_list:
        html_parts.append("</ul>")

    body = "\n".join(html_parts)
    safe_title = html_mod.escape(title)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>
body {{ font-family: Georgia, "Times New Roman", serif; font-size: 1em; line-height: 1.7; margin: 2em; color: #1a1a1a; }}
h1 {{ font-family: Helvetica, Arial, sans-serif; font-size: 1.5em; margin-bottom: 1em; border-bottom: 1px solid #ccc; padding-bottom: 0.3em; }}
h2 {{ font-family: Helvetica, Arial, sans-serif; font-size: 1.2em; margin-top: 1.5em; margin-bottom: 0.5em; color: #333; }}
p {{ margin-bottom: 0.8em; text-align: justify; }}
ul {{ margin: 0.5em 0 1em 1.5em; }}
li {{ margin-bottom: 0.4em; }}
blockquote {{ margin: 1em 2em; font-style: italic; color: #444; }}
</style>
</head>
<body>
<h1>{safe_title}</h1>
{body}
</body>
</html>"""


# ---------------------------------------------------------------------------
# File processing pipeline
# ---------------------------------------------------------------------------

def try_calibre_convert(file_path: Path, output_path: Path) -> bool:
    """Try using Calibre's ebook-convert for high-quality conversion.

    Returns True if conversion succeeded.
    """
    if not which("ebook-convert"):
        return False

    try:
        result = subprocess.run(
            ["ebook-convert", str(file_path), str(output_path),
             "--output-profile", "kindle"],
            capture_output=True, text=True, timeout=60,
        )
        return result.returncode == 0 and output_path.exists()
    except (subprocess.TimeoutExpired, Exception):
        return False


def process_pdf(file_path: Path, title: str) -> Path | None:
    """Extract text from PDF and convert to clean Kindle HTML.

    Tries pymupdf first, then pdfplumber. Returns path to processed
    HTML file, or None if extraction fails (send as-is).
    """
    full_text = None

    # Try pymupdf (fitz)
    try:
        import fitz
        doc = fitz.open(str(file_path))
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        candidate = "\n\n".join(text_parts)
        if candidate.strip():
            full_text = candidate
    except ImportError:
        pass

    # Fallback: pdfplumber
    if full_text is None:
        try:
            import pdfplumber
            with pdfplumber.open(str(file_path)) as pdf:
                text_parts = [page.extract_text() or "" for page in pdf.pages]
            candidate = "\n\n".join(text_parts)
            if candidate.strip():
                full_text = candidate
        except ImportError:
            pass

    if not full_text or len(full_text.strip()) < 50:
        # Scanned/image PDF or no extraction library — can't process
        return None

    cleaned = clean_extracted_text(full_text)
    html_content = text_to_html(cleaned, title)

    out_path = Path("/tmp/kindle") / f"{file_path.stem}_kindle.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_content, encoding="utf-8")
    print(f"  Extracted text from PDF -> clean HTML ({len(cleaned)} chars)")
    return out_path


def process_docx(file_path: Path, title: str) -> Path | None:
    """Extract text from DOCX and convert to clean Kindle HTML."""
    try:
        from docx import Document
    except ImportError:
        return None

    doc = Document(str(file_path))
    text_parts = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            text_parts.append(text)

    full_text = "\n\n".join(text_parts)
    if len(full_text.strip()) < 50:
        return None

    cleaned = clean_extracted_text(full_text)
    html_content = text_to_html(cleaned, title)

    out_path = Path("/tmp/kindle") / f"{file_path.stem}_kindle.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_content, encoding="utf-8")
    print(f"  Extracted text from DOCX -> clean HTML ({len(cleaned)} chars)")
    return out_path


def process_file(file_path: Path, title: str, raw: bool = False) -> tuple[Path, bool]:
    """Process a file for optimal Kindle reading.

    Returns (path_to_send, is_temp) where is_temp=True means the file
    should be cleaned up after sending.

    Pipeline:
    1. If --raw, skip all processing
    2. For PDF/DOCX: try Calibre -> try Python extraction -> send as-is
    3. For TXT: convert to HTML
    4. Everything else: send as-is
    """
    suffix = file_path.suffix.lower()

    if raw:
        print(f"  Raw mode: skipping processing, sending as-is")
        return file_path, False

    # --- TXT -> HTML ---
    if suffix == ".txt":
        text = file_path.read_text(encoding="utf-8", errors="replace")
        doc_title = title or file_path.stem.replace("_", " ").replace("-", " ").title()
        html_content = text_to_html(text, doc_title)
        out = Path("/tmp/kindle") / f"{file_path.stem}_kindle.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html_content, encoding="utf-8")
        print(f"  Converted TXT -> clean HTML")
        return out, True

    # --- PDF ---
    if suffix == ".pdf":
        # Try Calibre first (best quality)
        epub_out = Path("/tmp/kindle") / f"{file_path.stem}_kindle.epub"
        epub_out.parent.mkdir(parents=True, exist_ok=True)
        if try_calibre_convert(file_path, epub_out):
            print(f"  Converted PDF -> EPUB via Calibre")
            return epub_out, True

        # Fallback: Python text extraction -> HTML
        result = process_pdf(file_path, title or file_path.stem.replace("_", " ").title())
        if result:
            return result, True

        # Last resort: send PDF as-is
        print(f"  No PDF extraction library found. Sending PDF as-is.")
        print(f"  Tip: pip install pymupdf  OR  pip install pdfplumber  for text extraction")
        return file_path, False

    # --- DOCX / DOC ---
    if suffix in (".docx", ".doc"):
        # Try Calibre first
        epub_out = Path("/tmp/kindle") / f"{file_path.stem}_kindle.epub"
        epub_out.parent.mkdir(parents=True, exist_ok=True)
        if try_calibre_convert(file_path, epub_out):
            print(f"  Converted {suffix.upper()} -> EPUB via Calibre")
            return epub_out, True

        # Fallback: python-docx extraction (only works for .docx)
        if suffix == ".docx":
            result = process_docx(file_path, title or file_path.stem.replace("_", " ").title())
            if result:
                return result, True

        # Send as-is with Convert subject (Amazon will convert)
        print(f"  Sending {suffix.upper()} as-is (Amazon will convert)")
        return file_path, False

    # --- RTF ---
    if suffix == ".rtf":
        epub_out = Path("/tmp/kindle") / f"{file_path.stem}_kindle.epub"
        epub_out.parent.mkdir(parents=True, exist_ok=True)
        if try_calibre_convert(file_path, epub_out):
            print(f"  Converted RTF -> EPUB via Calibre")
            return epub_out, True
        print(f"  Sending RTF as-is (Amazon will convert)")
        return file_path, False

    # --- EPUB / MOBI / HTML / images: send as-is ---
    return file_path, False


# ---------------------------------------------------------------------------
# Send to Kindle
# ---------------------------------------------------------------------------

def send_to_kindle(file_path, convert=True, title=None, raw=False):
    """Process and send a file to Kindle via Gmail SMTP."""
    file_path = Path(file_path).resolve()

    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        return False

    suffix = file_path.suffix.lower()
    if suffix not in KINDLE_SUPPORTED:
        print(f"Warning: '{suffix}' may not be supported by Kindle. Sending anyway.")

    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb > 50:
        print(f"Error: File too large ({size_mb:.1f}MB). Kindle limit is 50MB.")
        return False

    app_password = get_app_password()
    if not app_password:
        print("Error: No Gmail App Password found in Keychain.")
        print(f"Run: python3 {__file__} --store-password 'YOUR_APP_PASSWORD'")
        print("Get one at: https://myaccount.google.com/apppasswords")
        return False

    # --- File processing pipeline ---
    print(f"Processing '{file_path.name}'...")
    processed_path, is_temp = process_file(
        file_path,
        title=title or file_path.stem.replace("_", " ").replace("-", " ").title(),
        raw=raw,
    )
    attachment_name = processed_path.name

    # Build email
    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = KINDLE_EMAIL
    msg["Subject"] = "Convert" if convert else "Kindle Document"
    msg.attach(MIMEText("Sent via kindle-agent", "plain"))

    content_type, _ = mimetypes.guess_type(str(processed_path))
    if content_type is None:
        content_type = "application/octet-stream"

    with open(processed_path, "rb") as f:
        attachment = MIMEApplication(f.read())
        attachment.add_header(
            "Content-Disposition", "attachment", filename=attachment_name
        )
        msg.attach(attachment)

    # Send
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, app_password)
            server.send_message(msg)
        print(f"Sent '{attachment_name}' to Kindle ({KINDLE_EMAIL})")
        return True
    except smtplib.SMTPAuthenticationError:
        print("Error: Gmail authentication failed. Check your App Password.")
        print("Generate one at: https://myaccount.google.com/apppasswords")
        return False
    except Exception as e:
        print(f"Error sending email: {e}")
        return False
    finally:
        if is_temp and processed_path.exists():
            processed_path.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send documents to Kindle")
    parser.add_argument("file", nargs="?", help="Path to the file to send")
    parser.add_argument("--no-convert", action="store_true",
                        help="Don't ask Amazon to convert (send as-is format)")
    parser.add_argument("--raw", action="store_true",
                        help="Skip all processing — send the file exactly as-is")
    parser.add_argument("--title", help="Title for the document")
    parser.add_argument("--store-password",
                        help="Store Gmail App Password in macOS Keychain")

    args = parser.parse_args()

    if args.store_password:
        if store_app_password(args.store_password):
            print("App Password stored in macOS Keychain")
        else:
            print("Error: Failed to store password")
        sys.exit(0)

    if not args.file:
        parser.print_help()
        sys.exit(1)

    success = send_to_kindle(
        args.file, convert=not args.no_convert, title=args.title, raw=args.raw,
    )
    sys.exit(0 if success else 1)
