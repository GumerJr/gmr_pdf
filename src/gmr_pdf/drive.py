"""Download de PDFs do Google Drive usando conta de serviço.

O PDF é retornado como ``bytes``, formato pronto para leitura direta
pelo PyMuPDF sem gravar nada em disco::

    from gmr_pdf.drive import get_drive_client
    import fitz  # PyMuPDF

    client = get_drive_client()
    pdf_bytes = client.download_pdf(file_id="...")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
"""

import io
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaIoBaseDownload

from gmr_pdf.logger import get_logger
from gmr_pdf.settings import Settings, get_settings

PDF_MIME_TYPE = "application/pdf"
PDF_MAGIC_BYTES = b"%PDF"

logger = get_logger(__name__)


def validate_pdf_bytes(data: bytes) -> bytes:
    """Valida se o conteúdo baixado é de fato um PDF."""
    if not data:
        logger.error("❌ Arquivo baixado está vazio")
        raise ValueError("O arquivo baixado está vazio.")
    if not data.startswith(PDF_MAGIC_BYTES):
        logger.error("❌ Conteúdo baixado não é PDF: magic bytes %r", data[:8])
        raise ValueError(
            "O conteúdo baixado não é um PDF válido "
            f"(magic bytes: {data[:8]!r})."
        )
    return data


class GoogleDriveClient:
    """Cliente de leitura do Google Drive autenticado via conta de serviço."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        credentials = service_account.Credentials.from_service_account_file(
            str(settings.service_account_file),
            scopes=settings.google_drive.scopes,
        )
        self._service: Resource = build(
            settings.google_drive.api_service,
            settings.google_drive.api_version,
            credentials=credentials,
            cache_discovery=False,
        )

    def list_pdfs(self, folder_id: str | None = None) -> list[dict[str, Any]]:
        """Lista os PDFs de uma pasta (usa a pasta do .env por padrão)."""
        folder = folder_id or self._settings.drive_folder_id
        logger.info("📂 Listando PDFs da pasta %s", folder)
        query = (
            f"'{folder}' in parents and "
            f"mimeType = '{PDF_MIME_TYPE}' and trashed = false"
        )
        files: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            response = (
                self._service.files()
                .list(
                    q=query,
                    pageSize=self._settings.google_drive.list_page_size,
                    fields="nextPageToken, files(id, name, size, modifiedTime)",
                    pageToken=page_token,
                )
                .execute()
            )
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        logger.info("📄 %d PDF(s) encontrado(s) na pasta", len(files))
        return files

    def download_pdf(self, file_id: str) -> bytes:
        """Baixa um PDF e retorna os bytes prontos para o PyMuPDF."""
        logger.info("⬇️  Iniciando download do arquivo %s", file_id)
        request = self._service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(
            buffer,
            request,
            chunksize=self._settings.google_drive.download_chunk_size_bytes,
        )
        done = False
        while not done:
            _, done = downloader.next_chunk()
        data = validate_pdf_bytes(buffer.getvalue())
        logger.info("✅ Download concluído: %s (%d bytes)", file_id, len(data))
        return data


def get_drive_client(settings: Settings | None = None) -> GoogleDriveClient:
    """Factory do cliente usando as configurações centralizadas."""
    return GoogleDriveClient(settings or get_settings())
