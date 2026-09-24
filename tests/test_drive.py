"""Testes unitários do módulo de download do Google Drive."""

import io
from pathlib import Path

import pytest

from gmr_pdf.drive import GoogleDriveClient, validate_pdf_bytes
from gmr_pdf.settings import (
    AppConfig,
    GoogleDriveConfig,
    Settings,
)


class TestValidatePdfBytes:
    def test_valid_pdf(self) -> None:
        data = b"%PDF-1.7\nconteudo qualquer"
        assert validate_pdf_bytes(data) is data

    def test_empty_data_raises(self) -> None:
        with pytest.raises(ValueError, match="vazio"):
            validate_pdf_bytes(b"")

    def test_non_pdf_raises(self) -> None:
        with pytest.raises(ValueError, match="não é um PDF"):
            validate_pdf_bytes(b"<html>not a pdf</html>")


def _settings() -> Settings:
    return Settings(
        app=AppConfig(),
        google_drive=GoogleDriveConfig(),
        service_account_file=Path("fake.json"),
        drive_folder_id="folder-123",
    )


def test_download_pdf_retorna_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """O download deve encadear chunks até completar e validar o PDF."""
    client = GoogleDriveClient.__new__(GoogleDriveClient)
    client._settings = _settings()

    payload = b"%PDF-1.7\n" + b"x" * 2048

    class FakeDownloader:
        def __init__(self, fh: io.BytesIO, request: object, chunksize: int) -> None:
            self._fh = fh
            self._payload = payload
            self._sent = False

        def next_chunk(self) -> tuple[None, bool]:
            if not self._sent:
                self._fh.write(self._payload)
                self._sent = True
                return None, False
            return None, True

    class FakeFiles:
        def get_media(self, fileId: str) -> object:
            assert fileId == "file-abc"
            return object()

    class FakeService:
        def files(self) -> FakeFiles:
            return FakeFiles()

    client._service = FakeService()  # type: ignore[assignment]
    monkeypatch.setattr("gmr_pdf.drive.MediaIoBaseDownload", FakeDownloader)

    assert client.download_pdf("file-abc") == payload
