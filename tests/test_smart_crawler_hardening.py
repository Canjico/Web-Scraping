import http.server
import os
import pathlib
import socketserver
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from source_registry import SourceConfig
from smart_crawler import (
    _HTMLAnalyzer,
    SmartCrawler,
    extract_deadline_dates,
    extract_embedded_urls,
    host_key,
    is_document_url,
    looks_like_document_url,
)


class SmartCrawlerHardeningTests(unittest.TestCase):
    def test_deadline_date_prefers_submission_context_over_later_timeline(self):
        text = (
            "Inscrições abertas até 21/05/2026. "
            "Resultado preliminar em 30/09/2026 e resultado final em 30/11/2026."
        )
        self.assertEqual([d.isoformat() for d in extract_deadline_dates(text)], ["2026-05-21"])

    def test_document_detection_without_extension_uses_context(self):
        self.assertTrue(looks_like_document_url("https://exemplo.org/download?id=123", "Baixar edital PDF"))
        self.assertTrue(is_document_url("https://exemplo.org/api/arquivo?filename=edital.pdf"))

    def test_embedded_relative_urls_are_found(self):
        html = '''
        <script>
          window.__DATA__ = {"arquivo":"/downloads/edital?id=123", "anexo":"../docs/regulamento.pdf"};
        </script>
        '''
        urls = extract_embedded_urls(html, "https://exemplo.org/chamadas/abertas")
        self.assertIn("https://exemplo.org/downloads/edital?id=123", urls)
        self.assertIn("https://exemplo.org/docs/regulamento.pdf", urls)

    def test_html_analyzer_reads_data_url_and_onclick_links(self):
        parser = _HTMLAnalyzer("https://exemplo.org")
        parser.feed('''
          <button data-url="/download?id=1" title="Baixar edital">Baixar</button>
          <button onclick="window.open('/arquivo/2')">Anexo</button>
          <form action="/busca/editais"></form>
        ''')
        hrefs = {item["href"] for item in parser.links}
        self.assertIn("/download?id=1", hrefs)
        self.assertIn("/arquivo/2", hrefs)
        self.assertIn("/busca/editais", hrefs)

    def test_host_key_ignores_port(self):
        self.assertEqual(host_key("http://127.0.0.1:8080/index.html"), "127.0.0.1")

    def test_local_crawl_detects_hidden_documents_and_candidate(self):
        old_env = {
            "SMART_USE_SITEMAP": os.environ.get("SMART_USE_SITEMAP"),
            "SMART_CRAWLER_SLEEP": os.environ.get("SMART_CRAWLER_SLEEP"),
            "SMART_CRAWLER_TIMEOUT": os.environ.get("SMART_CRAWLER_TIMEOUT"),
            "SMART_USE_PLAYWRIGHT": os.environ.get("SMART_USE_PLAYWRIGHT"),
        }
        os.environ["SMART_USE_SITEMAP"] = "0"
        os.environ["SMART_USE_PLAYWRIGHT"] = "0"
        os.environ["SMART_CRAWLER_SLEEP"] = "0"
        os.environ["SMART_CRAWLER_TIMEOUT"] = "5"

        root = tempfile.TemporaryDirectory()
        base = pathlib.Path(root.name)
        try:
            html = """
            <html><head><title>Editais abertos</title></head><body>
              <h1>Chamada Pública de Inovação</h1>
              <p>Inscrições abertas até 21/05/2026.</p>
              <button data-url="/download?id=123" title="Baixar edital PDF">PDF</button>
              <script>window.__DATA__={"anexo":"/docs/regulamento.pdf"}</script>
            </body></html>
            """
            (base / "index.html").write_text(html, encoding="utf-8")
            (base / "docs").mkdir()
            (base / "docs" / "regulamento.pdf").write_bytes(b"%PDF-1.4 fake")

            class Handler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_HEAD(self):
                    if self.path.startswith("/download"):
                        self.send_response(200)
                        self.send_header("Content-Type", "application/pdf")
                        self.send_header("Content-Disposition", 'attachment; filename="edital.pdf"')
                        self.end_headers()
                        return
                    return super().do_HEAD()

                def do_GET(self):
                    if self.path.startswith("/download"):
                        self.send_response(200)
                        self.send_header("Content-Type", "application/pdf")
                        self.end_headers()
                        self.wfile.write(b"%PDF fake")
                        return
                    return super().do_GET()

            old_cwd = os.getcwd()
            os.chdir(root.name)
            server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                source = SourceConfig(
                    script="TEST.py",
                    name="TEST",
                    base_url=f"http://127.0.0.1:{port}",
                    start_urls=(f"http://127.0.0.1:{port}/index.html",),
                    allowed_domains=("127.0.0.1",),
                    keywords=("inovacao",),
                )
                report = SmartCrawler(source).crawl()
                self.assertEqual(report.pages_fetched, 1)
                self.assertGreaterEqual(report.documents_seen, 2)
                self.assertGreaterEqual(len(report.candidates), 1)
            finally:
                server.shutdown()
                server.server_close()
                os.chdir(old_cwd)
        finally:
            root.cleanup()
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
