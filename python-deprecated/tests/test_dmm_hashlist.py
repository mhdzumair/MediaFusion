import json

from workers.scrapers.dmm_hashlist import (
    HashlistTorrentEntry,
    decode_hashlist_payload,
    deduplicate_entries_by_info_hash,
    extract_hash_fragment_from_html,
    is_likely_anime_title,
    is_likely_sports_broadcast_title,
    is_valid_metadata_match,
)


def test_extract_hash_fragment_from_html():
    html = """
    <html>
      <body>
        <iframe src="https://debridmediamanager.com/hashlist#abc123"></iframe>
      </body>
    </html>
    """
    assert extract_hash_fragment_from_html(html) == "abc123"


def test_decode_hashlist_payload_real_object_shape():
    encoded_payload = (
        "N4IgLglmA2CmIC4QHUCG0DWEB2BzABACayqEgA04A9gE42zZgDOiA2qAGYRzaoC28JABUAFrHxpMOAgBES"
        "hfAGUSTKtnwAWfAAoAjAAYAHPoAO+AB4AmAGwBWfACkqATwCUFECNRMRiEAE5YXVQAZgAjAGNbf39bW0sI"
        "1F1-DmsQ2wiNS11rWAB2MNh0vI0Q3UJDXQiPMOcwWBYEQ0NbUpj-a10AXwBdLqA"
    )
    entries = decode_hashlist_payload(encoded_payload)
    assert len(entries) == 1
    assert entries[0].info_hash == "9e1a3bc599552ca19f635c4216e7be357431d81c"
    assert entries[0].size == 8854399961


def test_decode_hashlist_payload_with_object_wrapper(monkeypatch):
    payload = {
        "title": "Example",
        "torrents": [
            {
                "filename": "Example.Movie.2025.1080p.WEB-DL",
                "hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "bytes": 1234,
            }
        ],
    }
    monkeypatch.setattr(
        "scrapers.dmm_hashlist.decompress_from_encoded_uri_component",
        lambda _: json.dumps(payload),
    )

    entries = decode_hashlist_payload("dummy")
    assert len(entries) == 1
    assert entries[0].filename == "Example.Movie.2025.1080p.WEB-DL"
    assert entries[0].info_hash == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert entries[0].size == 1234


def test_decode_hashlist_payload_with_array_wrapper(monkeypatch):
    payload = [
        {
            "filename": "Example.Series.S01E01.1080p.WEB-DL",
            "hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "bytes": 4321,
        },
        {
            "filename": "Invalid.Hash.Entry",
            "hash": "not_a_hash",
            "bytes": 1,
        },
    ]
    monkeypatch.setattr(
        "scrapers.dmm_hashlist.decompress_from_encoded_uri_component",
        lambda _: json.dumps(payload),
    )

    entries = decode_hashlist_payload("dummy")
    assert len(entries) == 1
    assert entries[0].info_hash == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert entries[0].size == 4321


def test_deduplicate_entries_by_info_hash():
    entries = [
        HashlistTorrentEntry("First", "cccccccccccccccccccccccccccccccccccccccc", 10),
        HashlistTorrentEntry("Duplicate", "cccccccccccccccccccccccccccccccccccccccc", 20),
        HashlistTorrentEntry("Second", "dddddddddddddddddddddddddddddddddddddddd", 30),
    ]

    deduped = deduplicate_entries_by_info_hash(entries)
    assert len(deduped) == 2
    assert [entry.info_hash for entry in deduped] == [
        "cccccccccccccccccccccccccccccccccccccccc",
        "dddddddddddddddddddddddddddddddddddddddd",
    ]


def test_is_valid_metadata_match_rejects_year_mismatch_movie():
    assert (
        is_valid_metadata_match(
            parsed_title="Vixen",
            parsed_year=2022,
            media_type="movie",
            candidate={
                "title": "Vixen Highway",
                "year": 2001,
                "type": "movie",
            },
        )
        is False
    )


def test_is_valid_metadata_match_rejects_title_mismatch():
    assert (
        is_valid_metadata_match(
            parsed_title="Arco",
            parsed_year=2025,
            media_type="movie",
            candidate={
                "title": "Giovanna D'Arco",
                "year": 2014,
                "type": "movie",
            },
        )
        is False
    )


def test_is_valid_metadata_match_accepts_confident_movie_match():
    assert (
        is_valid_metadata_match(
            parsed_title="Interstellar",
            parsed_year=2014,
            media_type="movie",
            candidate={
                "title": "Interstellar",
                "year": 2014,
                "type": "movie",
            },
        )
        is True
    )


def test_is_likely_sports_broadcast_title_detects_known_patterns():
    assert is_likely_sports_broadcast_title("F1.2025.R01.Australian.Grand.Prix.SkyF1HD.1080P") is True
    assert is_likely_sports_broadcast_title("NBA.2025.02.18.Lakers.vs.Celtics.1080p") is True
    assert is_likely_sports_broadcast_title("UFC.313.Prelims.1080p.WEB.h264") is True
    assert is_likely_sports_broadcast_title("WWE.Raw.2025.02.24.1080p.WEB.h264") is True


def test_is_valid_metadata_match_rejects_sports_broadcast_movie():
    assert (
        is_valid_metadata_match(
            parsed_title="F1",
            parsed_year=2025,
            media_type="movie",
            candidate={
                "title": "F1",
                "year": 2025,
                "type": "movie",
            },
            torrent_title="F1.2025.R01.Australian.Grand.Prix.SkyF1HD.1080P",
        )
        is False
    )


def test_is_likely_anime_title_detects_fansub_release():
    assert is_likely_sports_broadcast_title("[SubsPlease] Solo Leveling - 20 (1080p)") is False
    assert is_likely_anime_title("[SubsPlease] Solo Leveling - 20 (1080p)", media_type="series") is True


def test_is_likely_anime_title_detects_high_episode_number_series():
    assert is_likely_anime_title("One.Piece.1089.1080p.WEB", media_type="series") is True


def test_is_likely_anime_title_avoids_movie_year_and_resolution_false_positive():
    assert is_likely_anime_title("Oppenheimer.2023.2160p.WEB-DL", media_type="movie") is False
