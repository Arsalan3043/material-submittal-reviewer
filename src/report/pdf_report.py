from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.models.findings import ReviewReport, Severity

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _severity_label(severity: str) -> str:
    mapping = {
        Severity.CRITICAL.value: "CRITICAL",
        Severity.WARNING.value: "WARNING",
        Severity.PASS.value: "PASS",
    }
    return mapping.get(severity, severity.upper())


def render_report_html(report: ReviewReport) -> str:
    """Render the ReviewReport to an HTML string using the Jinja2 template."""
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)
    env.filters["severity_label"] = _severity_label
    template = env.get_template("report.html")
    return template.render(report=report)


def generate_pdf(report: ReviewReport, output_path: str | Path) -> Path:
    """
    Render report to HTML then convert to PDF via WeasyPrint.
    Returns the path to the generated PDF file.
    """
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise RuntimeError(
            "WeasyPrint is required for PDF generation. Install it with: pip install weasyprint"
        ) from exc

    html_string = render_report_html(report)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_string, base_url=str(_TEMPLATES_DIR)).write_pdf(str(output))
    return output


def generate_pdf_bytes(report: ReviewReport) -> bytes:
    """Return the PDF as raw bytes (for Streamlit download_button)."""
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise RuntimeError(
            "WeasyPrint is required for PDF generation. Install it with: pip install weasyprint"
        ) from exc

    html_string = render_report_html(report)
    return HTML(string=html_string, base_url=str(_TEMPLATES_DIR)).write_pdf()
