import re
import pytest

from app.agents.soulcare_team import search_song
from app.core.config import settings


YOUTUBE_URL_RE = re.compile(r"<youtube_url>https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}</youtube_url>")


@pytest.mark.parametrize(
    "query",
    [
        # Genre based
        "lofi hip hop",
        "classical piano",
        "jazz instrumental",
        # Emotion / mood based
        "uplifting happy song",
        "sad emotional ballad",
        "motivational workout music",
        # Decade / era
        "80s pop",
        "90s rock anthem",
        # Language / region
        "k-pop upbeat",
        "latin romantic song",
        # Rich natural language prompt
        "a comforting song for a rainy day to relax",
    ],
)
def test_search_song_various_queries(query):
    """Ensure search_song handles a variety of search intents gracefully.

    If a YOUTUBE_API_KEY is configured and valid, we expect a youtube_url tag.
    Otherwise we expect an informative message (non-empty string).
    """
    result = search_song(query)
    print(f"search_song query='{query}' -> result='{result}'")
    assert isinstance(result, str) and len(result) > 0

    api_key_present = bool(settings.youtube_api_key)
    if api_key_present:
        # With a key, we expect a valid YouTube URL wrapped in the tag
        assert YOUTUBE_URL_RE.match(result) or result in {
            "No matching song found on YouTube.",
            "Failed to search YouTube.",
        }
    else:
        # Without a key, we expect a helpful message
        assert result == "YouTube search unavailable. Please configure YOUTUBE_API_KEY."


def test_search_song_empty_query():
    result = search_song("")
    assert isinstance(result, str) and len(result) > 0

