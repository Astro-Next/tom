import hashlib

import pytest

from tom.attachments import (
    _hash_url,
    cache_path,
    extract_attachment_urls,
)


class TestHashUrl:
    def test_deterministic(self):
        assert _hash_url("https://example.com/img.png") == _hash_url("https://example.com/img.png")

    def test_twelve_chars(self):
        assert len(_hash_url("https://example.com/img.png")) == 12

    def test_different_urls(self):
        assert _hash_url("https://a.com/1.png") != _hash_url("https://a.com/2.png")


class TestExtractAttachmentUrls:
    def test_img_tag(self):
        text = '<img src="https://github.com/user-attachments/assets/abc.png">'
        result = extract_attachment_urls(text)
        assert len(result) == 1
        assert result[0][0] == "https://github.com/user-attachments/assets/abc.png"
        assert result[0][1] == ".png"

    def test_markdown_image(self):
        text = '![screenshot](https://github.com/user-attachments/assets/abc.png)'
        result = extract_attachment_urls(text)
        assert len(result) == 1
        assert result[0][1] == ".png"

    def test_markdown_image_no_extension_defaults_png(self):
        text = '![screenshot](https://github.com/user-attachments/assets/abc123)'
        result = extract_attachment_urls(text)
        assert len(result) == 1
        assert result[0][1] == ".png"

    def test_file_link_with_extension(self):
        text = '[log](https://github.com/user-attachments/files/abc.log)'
        result = extract_attachment_urls(text)
        assert len(result) == 1
        assert result[0][1] == ".log"

    def test_file_link_no_extension_skipped(self):
        text = '[readme](https://github.com/user-attachments/files/abc123)'
        result = extract_attachment_urls(text)
        assert len(result) == 0

    def test_deduplicates(self):
        url = "https://github.com/user-attachments/assets/abc.png"
        text = f'![a]({url})\n![b]({url})'
        result = extract_attachment_urls(text)
        assert len(result) == 1

    def test_multiple_mixed(self):
        text = (
            '![img](https://github.com/user-attachments/assets/a.png)\n'
            '<img src="https://github.com/user-attachments/assets/b.jpg">\n'
            '[file](https://github.com/user-attachments/files/c.pdf)\n'
        )
        result = extract_attachment_urls(text)
        assert len(result) == 3
        extensions = [r[1] for r in result]
        assert ".png" in extensions
        assert ".jpg" in extensions
        assert ".pdf" in extensions

    def test_no_attachments(self):
        assert extract_attachment_urls("Just some text with no URLs") == []

    def test_non_github_links_ignored(self):
        text = '[link](https://example.com/file.txt)'
        result = extract_attachment_urls(text)
        assert len(result) == 0


class TestCachePath:
    def test_format(self):
        path = cache_path("proj123", 42, "https://example.com/img.png", ".png")
        assert str(path).startswith("/tmp/tom-proj123/cache/42/")
        assert str(path).endswith(".png")
        assert len(path.stem) == 12
