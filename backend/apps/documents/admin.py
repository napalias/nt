from django.contrib import admin

from apps.documents.models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "url",
        "file_size",
        "content_hash",
        "extracted_at",
        "created_at",
    ]
    search_fields = ["title", "url", "content_hash"]
    readonly_fields = ["created_at"]
    list_per_page = 50
