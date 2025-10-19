# -*- coding: utf-8 -*-
import logging
import os
import re
import unicodedata
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote

from requests import Session
from requests.exceptions import RequestException
from subliminal import ProviderError
from subliminal.video import Movie
from subliminal.subtitle import fix_line_ending
from subzero.language import Language

from subliminal_patch.providers import Provider
from subliminal_patch.providers.utils import update_matches
from subliminal_patch.subtitle import Subtitle

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.subt.is/v1"


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug


class SubtisSubtitle(Subtitle):
    provider_name = "subtis"
    hash_verifiable = False

    def __init__(self, language: Language, video: Movie, payload: Dict):
        download_url = payload["subtitle"]["subtitle_link"]
        super(SubtisSubtitle, self).__init__(language, hearing_impaired=False, page_link=download_url)

        self.video = video
        self.payload = payload

        subtitle_info = payload.get("subtitle") or {}
        release_group = (payload.get("release_group") or {}).get("release_group_name")

        self.subtitle_id = subtitle_info.get("id")
        self.download_url = download_url
        self.release_group = release_group
        self.subtitle_file_name = subtitle_info.get("subtitle_file_name")
        self.title_file_name = subtitle_info.get("title_file_name")
        self.rip_type = subtitle_info.get("rip_type")
        self.resolution = subtitle_info.get("resolution")
        self.external_id = subtitle_info.get("external_id")
        self.title_metadata = payload.get("title") or {}

        info_parts = [
            part
            for part in (
                self.subtitle_file_name,
                self.title_file_name,
                self.rip_type,
                self.resolution,
                release_group,
            )
            if part
        ]
        self.release_info = " | ".join(info_parts)

    @property
    def id(self) -> str:
        return str(self.subtitle_id)

    def get_matches(self, video: Movie) -> Iterable[str]:
        matches = set()

        title_name = (self.title_metadata.get("title_name") or "").lower()
        if video.title and video.title.lower() == title_name:
            matches.add("title")

        if video.year and self.title_metadata.get("year") == video.year:
            matches.add("year")

        imdb_id = self.title_metadata.get("imdb_id")
        if imdb_id and video.imdb_id and str(video.imdb_id) == str(imdb_id):
            matches.add("imdb_id")
            matches.add("title")
            matches.add("year")

        if video.release_group and self.release_group:
            if video.release_group.lower() in self.release_group.lower():
                matches.add("release_group")

        if video.resolution and self.resolution and video.resolution.lower() in self.resolution.lower():
            matches.add("resolution")

        if video.source and self.rip_type and video.source.lower() in self.rip_type.lower():
            matches.add("source")

        update_matches(
            matches,
            video,
            [
                self.release_info,
                self.subtitle_file_name or "",
                self.title_file_name or "",
            ],
        )

        return matches


class SubtisProvider(Provider):
    provider_name = "subtis"
    hash_verifiable = False

    _base_languages = {
        Language.fromalpha2("es"),
        Language("spa", "ES"),
        Language("spa", "MX"),
    }
    languages = set(_base_languages)
    languages.update(Language.rebuild(lang, forced=True) for lang in _base_languages)
    languages.update(Language.rebuild(lang, hi=True) for lang in _base_languages)

    video_types = (Movie,)
    subtitle_class = SubtisSubtitle

    def __init__(self):
        self.session: Optional[Session] = None

    def initialize(self):
        self.session = Session()

    def terminate(self):
        if self.session is not None:
            self.session.close()
            self.session = None

    def list_subtitles(self, video: Movie, languages: Iterable[Language]):
        languages = list(languages)
        if not isinstance(video, Movie):
            logger.debug("Subtis provider only supports movies, got %s", type(video))
            return []

        if not any(language in self.languages for language in languages):
            logger.debug("Requested languages %s not supported by Subtis", languages)
            return []

        preferred_language = self._select_language(languages)

        try:
            subtitles = self._search_by_file(video, preferred_language)
        except ProviderError as error:
            logger.error("Subtis exact match query failed: %s", error)
            subtitles = []

        if subtitles:
            return subtitles

        try:
            return self._search_by_slug(video, preferred_language)
        except ProviderError as error:
            logger.error("Subtis slug query failed: %s", error)
            return []

    def download_subtitle(self, subtitle: SubtisSubtitle):
        if self.session is None:
            raise ProviderError("Session not initialized")

        logger.debug("Downloading subtitle %s from %s", subtitle.id, subtitle.download_url)

        try:
            response = self.session.get(subtitle.download_url, timeout=30)
            response.raise_for_status()
        except RequestException as error:
            logger.error("Unable to download subtitle %s: %s", subtitle.id, error)
            raise ProviderError("Failed to download subtitle from Subtis") from error

        subtitle.content = fix_line_ending(response.content)

    def _search_by_file(self, video: Movie, language: Language):
        if self.session is None:
            raise ProviderError("Session not initialized")

        if not video.size or not video.name:
            logger.debug("Video size or name not available for %s, skipping file match", video)
            return []

        file_size = int(video.size)
        filename = os.path.basename(video.name)
        encoded_filename = quote(filename, safe="")
        url = f"{API_BASE_URL}/subtitle/file/name/{file_size}/{encoded_filename}"

        logger.debug("Requesting Subtis exact match for %s (%s bytes)", filename, file_size)

        try:
            response = self.session.get(url, timeout=30)
            if response.status_code == 404:
                logger.debug("No exact match found on Subtis for %s", filename)
                return []
            response.raise_for_status()
            data = response.json()
        except RequestException as error:
            logger.error("Exact match request failed: %s", error)
            raise ProviderError("Subtis exact match request failed") from error
        except ValueError as error:
            logger.error("Invalid JSON response for exact match: %s", error)
            raise ProviderError("Invalid Subtis response for exact match") from error

        return [self._build_subtitle(video, language, data)]

    def _search_by_slug(self, video: Movie, language: Language):
        if self.session is None:
            raise ProviderError("Session not initialized")

        slug = self._build_slug(video)
        if not slug:
            logger.debug("Unable to derive slug for video %s", video)
            return []

        url = f"{API_BASE_URL}/subtitles/movie/{quote(slug, safe='')}"
        logger.debug("Requesting Subtis subtitles list for slug %s", slug)

        try:
            response = self.session.get(url, timeout=30)
            if response.status_code == 404:
                logger.debug("No subtitles found on Subtis for slug %s", slug)
                return []
            response.raise_for_status()
            data = response.json()
        except RequestException as error:
            logger.error("Slug search request failed: %s", error)
            raise ProviderError("Subtis slug search request failed") from error
        except ValueError as error:
            logger.error("Invalid JSON response for slug search: %s", error)
            raise ProviderError("Invalid Subtis response for slug search") from error

        results = data.get("results") or []
        subtitles = [self._build_subtitle(video, language, item) for item in results]

        return subtitles

    def _build_slug(self, video: Movie) -> Optional[str]:
        if not video.title:
            return None

        slug = _slugify(video.title)
        if video.year:
            slug = f"{slug}-{video.year}"

        return slug

    def _build_subtitle(self, video: Movie, language: Language, payload: Dict) -> SubtisSubtitle:
        return self.subtitle_class(language, video, payload)

    def _select_language(self, languages: List[Language]) -> Language:
        preferred = Language.fromalpha2("es")

        for language in languages:
            if language in self.languages and not language.forced and not language.hi:
                return language

        for language in languages:
            if language in self.languages:
                return Language.rebuild(language, forced=False, hi=False)

        return preferred
