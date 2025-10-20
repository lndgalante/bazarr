# -*- coding: utf-8 -*-
import pytest
from subliminal_patch.core import Movie
from subliminal_patch.providers.subtis import SubtisProvider
from subliminal_patch.providers.subtis import SubtisSubtitle
from subzero.language import Language


@pytest.fixture
def provider():
    with SubtisProvider() as provider:
        yield provider


@pytest.fixture
def spanish_movie():
    return Movie(
        "/path/to/movie.mkv",
        "Volver",
        year=2006,
        imdb_id="tt0441909",
    )


def test_init(provider):
    """Test provider initialization."""
    assert provider is not None
    assert provider.provider_name == "subtis"
    assert not provider.hash_verifiable


def test_languages():
    """Test that provider supports expected languages."""
    provider = SubtisProvider()

    # Should support Spanish variants
    assert Language.fromalpha2("es") in provider.languages
    assert Language("spa", "ES") in provider.languages
    assert Language("spa", "MX") in provider.languages

    # Should support forced variants
    assert Language.rebuild(Language.fromalpha2("es"), forced=True) in provider.languages

    # Should support HI variants
    assert Language.rebuild(Language.fromalpha2("es"), hi=True) in provider.languages


def test_video_types():
    """Test that provider only supports movies."""
    provider = SubtisProvider()
    assert provider.video_types == (Movie,)


def test_select_language_prefers_non_forced_non_hi():
    """Test language selection prefers non-forced, non-HI languages."""
    provider = SubtisProvider()

    languages = [
        Language.rebuild(Language.fromalpha2("es"), forced=True),
        Language.rebuild(Language.fromalpha2("es"), hi=True),
        Language.fromalpha2("es"),  # This should be selected
        Language("spa", "MX"),
    ]

    selected = provider._select_language(languages)
    assert selected == Language.fromalpha2("es")
    assert not selected.forced
    assert not selected.hi


def test_select_language_fallback_to_first_supported():
    """Test language selection falls back to first supported language."""
    provider = SubtisProvider()

    # Only provide forced/HI variants
    languages = [
        Language.rebuild(Language.fromalpha2("es"), forced=True),
        Language.rebuild(Language("spa", "MX"), hi=True),
    ]

    selected = provider._select_language(languages)
    # Should rebuild without flags
    assert selected.alpha3 == "spa"
    assert not selected.forced
    assert not selected.hi


def test_select_language_default_fallback():
    """Test language selection default fallback."""
    provider = SubtisProvider()

    # Provide unsupported language
    languages = [Language.fromalpha2("fr")]

    selected = provider._select_language(languages)
    # Should fallback to Spanish
    assert selected == Language.fromalpha2("es")


def test_build_slug_with_title_and_year():
    """Test slug generation with title and year."""
    provider = SubtisProvider()
    video = Movie("/path/to/movie.mkv", "The Matrix", year=1999)

    slug = provider._build_slug(video)
    assert slug == "the-matrix-1999"


def test_build_slug_with_title_only():
    """Test slug generation with title only."""
    provider = SubtisProvider()
    video = Movie("/path/to/movie.mkv", "Amélie")

    slug = provider._build_slug(video)
    assert slug == "amelie"


def test_build_slug_no_title():
    """Test slug generation with no title."""
    provider = SubtisProvider()
    video = Movie("/path/to/movie.mkv", None)

    slug = provider._build_slug(video)
    assert slug is None


def test_subtitle_get_matches_title(spanish_movie):
    """Test subtitle matching on title."""
    payload = {
        "subtitle": {"subtitle_link": "http://example.com/sub.srt", "id": "123"},
        "title": {"title_name": "Volver", "year": 2006, "imdb_id": "tt0441909"},
        "release_group": None,
    }

    subtitle = SubtisSubtitle(Language.fromalpha2("es"), spanish_movie, payload)
    matches = subtitle.get_matches(spanish_movie)

    assert "title" in matches
    assert "year" in matches
    assert "imdb_id" in matches


def test_subtitle_get_matches_release_group(spanish_movie):
    """Test subtitle matching on release group."""
    payload = {
        "subtitle": {
            "subtitle_link": "http://example.com/sub.srt",
            "id": "123",
            "resolution": "1080p",
            "rip_type": "BluRay",
        },
        "title": {"title_name": "Volver"},
        "release_group": {"release_group_name": "SPARKS"},
    }

    spanish_movie.release_group = "SPARKS"
    spanish_movie.resolution = "1080p"
    spanish_movie.source = "Blu-ray"

    subtitle = SubtisSubtitle(Language.fromalpha2("es"), spanish_movie, payload)
    matches = subtitle.get_matches(spanish_movie)

    assert "release_group" in matches
    assert "resolution" in matches
    assert "source" in matches


def test_subtitle_id():
    """Test subtitle ID property."""
    video = Movie("/path/to/movie.mkv", "Test Movie")
    payload = {
        "subtitle": {"subtitle_link": "http://example.com/sub.srt", "id": "12345"},
        "title": {},
        "release_group": None,
    }

    subtitle = SubtisSubtitle(Language.fromalpha2("es"), video, payload)
    assert subtitle.id == "12345"


def test_subtitle_release_info():
    """Test subtitle release info construction."""
    video = Movie("/path/to/movie.mkv", "Test Movie")
    payload = {
        "subtitle": {
            "subtitle_link": "http://example.com/sub.srt",
            "id": "123",
            "subtitle_file_name": "movie.srt",
            "title_file_name": "movie.mkv",
            "rip_type": "WEB-DL",
            "resolution": "1080p",
        },
        "title": {},
        "release_group": {"release_group_name": "AMZN"},
    }

    subtitle = SubtisSubtitle(Language.fromalpha2("es"), video, payload)
    assert "movie.srt" in subtitle.release_info
    assert "movie.mkv" in subtitle.release_info
    assert "WEB-DL" in subtitle.release_info
    assert "1080p" in subtitle.release_info
    assert "AMZN" in subtitle.release_info
