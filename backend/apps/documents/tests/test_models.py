import pytest
from model_bakery import baker

from apps.documents.models import Document


@pytest.mark.django_db
class TestDocument:
    def test_create_document(self):
        doc = baker.make(
            Document,
            url="https://www.tpdris.lt/docs/test.pdf",
            title="Detalusis planas Nr. 123",
            storage_path="/docs/test.pdf",
            content_hash="abc123def456",
        )
        assert doc.pk is not None
        assert str(doc) == "Detalusis planas Nr. 123"
        assert doc.created_at is not None

    def test_str_falls_back_to_url(self):
        doc = baker.make(
            Document,
            url="https://www.tpdris.lt/docs/test.pdf",
            title="",
        )
        assert str(doc) == "https://www.tpdris.lt/docs/test.pdf"

    def test_content_hash_indexed(self):
        """content_hash field should be indexed for fast lookups."""
        field = Document._meta.get_field("content_hash")
        assert field.db_index is True

    def test_extracted_text_blank(self):
        doc = baker.make(
            Document,
            url="https://example.com/doc.pdf",
            title="Test",
            extracted_text="",
        )
        assert doc.extracted_text == ""

    def test_file_size_nullable(self):
        doc = baker.make(
            Document,
            url="https://example.com/doc.pdf",
            title="Test",
            file_size=None,
        )
        assert doc.file_size is None

    def test_file_size_set(self):
        doc = baker.make(
            Document,
            url="https://example.com/doc.pdf",
            title="Test",
            file_size=1024000,
        )
        assert doc.file_size == 1024000
