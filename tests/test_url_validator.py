import pytest

from backend.services.url_validator import validate_crawl_url


@pytest.mark.parametrize(
    "url",
    ["file:///tmp/a", "ftp://example.com/a", "http://127.0.0.1/", "http://169.254.169.254/"],
)
def test_unsafe_urls_are_rejected(url):
    with pytest.raises(ValueError):
        validate_crawl_url(url)

