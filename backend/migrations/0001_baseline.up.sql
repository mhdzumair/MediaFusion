-- PostgreSQL database dump


-- Dumped from database version 18.1
-- Dumped by pg_dump version 18.1

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


-- Name: contributionstatus; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.contributionstatus AS ENUM (
    'PENDING',
    'APPROVED',
    'REJECTED'
);


-- Name: downloadstatus; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.downloadstatus AS ENUM (
    'COMPLETED',
    'FAILED',
    'CANCELLED'
);


-- Name: filetype; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.filetype AS ENUM (
    'VIDEO',
    'AUDIO',
    'SUBTITLE',
    'ARCHIVE',
    'SAMPLE',
    'TRAILER',
    'NFO',
    'OTHER'
);


-- Name: historysource; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.historysource AS ENUM (
    'MEDIAFUSION',
    'TRAKT',
    'SIMKL',
    'MANUAL'
);


-- Name: integrationtype; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.integrationtype AS ENUM (
    'TRAKT',
    'SIMKL',
    'MAL',
    'LETTERBOXD',
    'ANILIST',
    'TVTIME'
);


-- Name: iptvsourcetype; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.iptvsourcetype AS ENUM (
    'M3U',
    'XTREAM',
    'STALKER'
);


-- Name: linksource; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.linksource AS ENUM (
    'USER',
    'PTT_PARSER',
    'TORRENT_METADATA',
    'DEBRID_REALDEBRID',
    'DEBRID_ALLDEBRID',
    'DEBRID_PREMIUMIZE',
    'DEBRID_TORBOX',
    'DEBRID_DEBRIDLINK',
    'MANUAL',
    'FILENAME'
);


-- Name: mediatype; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.mediatype AS ENUM (
    'MOVIE',
    'SERIES',
    'TV',
    'EVENTS'
);


-- Name: nuditystatus; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.nuditystatus AS ENUM (
    'NONE',
    'MILD',
    'MODERATE',
    'SEVERE',
    'UNKNOWN',
    'DISABLE'
);


-- Name: streamtype; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.streamtype AS ENUM (
    'TORRENT',
    'HTTP',
    'YOUTUBE',
    'USENET',
    'TELEGRAM',
    'EXTERNAL_LINK',
    'ACESTREAM'
);


-- Name: torrenttype; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.torrenttype AS ENUM (
    'PUBLIC',
    'SEMI_PRIVATE',
    'PRIVATE',
    'WEB_SEED'
);


-- Name: trackerstatus; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.trackerstatus AS ENUM (
    'WORKING',
    'FAILING',
    'UNKNOWN'
);


-- Name: userrole; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.userrole AS ENUM (
    'USER',
    'PAID_USER',
    'MODERATOR',
    'ADMIN'
);


-- Name: watchaction; Type: TYPE; Schema: public; Owner: -

CREATE TYPE public.watchaction AS ENUM (
    'WATCHED',
    'DOWNLOADED',
    'QUEUED'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

-- Name: acestream_stream; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.acestream_stream (
    id integer NOT NULL,
    stream_id integer NOT NULL,
    content_id character varying,
    info_hash character varying
);


-- Name: acestream_stream_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.acestream_stream_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: acestream_stream_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.acestream_stream_id_seq OWNED BY public.acestream_stream.id;


-- Name: aka_title; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.aka_title (
    id integer NOT NULL,
    title character varying NOT NULL,
    media_id integer NOT NULL,
    language_code character varying,
    title_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, (title)::text)) STORED NOT NULL
);


-- Name: aka_title_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.aka_title_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: aka_title_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.aka_title_id_seq OWNED BY public.aka_title.id;


-- Name: annotation_request_dismissal; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.annotation_request_dismissal (
    id integer NOT NULL,
    stream_id integer NOT NULL,
    media_id integer NOT NULL,
    dismissed_by character varying NOT NULL,
    dismiss_reason text,
    dismissed_at timestamp with time zone NOT NULL
);


-- Name: annotation_request_dismissal_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.annotation_request_dismissal_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: annotation_request_dismissal_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.annotation_request_dismissal_id_seq OWNED BY public.annotation_request_dismissal.id;


-- Name: audio_channel; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.audio_channel (
    id integer NOT NULL,
    name character varying NOT NULL
);


-- Name: audio_channel_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.audio_channel_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: audio_channel_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.audio_channel_id_seq OWNED BY public.audio_channel.id;


-- Name: audio_format; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.audio_format (
    id integer NOT NULL,
    name character varying NOT NULL
);


-- Name: audio_format_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.audio_format_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: audio_format_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.audio_format_id_seq OWNED BY public.audio_format.id;


-- Name: catalog; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.catalog (
    id integer NOT NULL,
    name character varying NOT NULL,
    display_name character varying,
    description character varying,
    is_system boolean NOT NULL,
    display_order integer NOT NULL
);


-- Name: catalog_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.catalog_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: catalog_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.catalog_id_seq OWNED BY public.catalog.id;


-- Name: contribution_settings; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.contribution_settings (
    id character varying NOT NULL,
    auto_approval_threshold integer NOT NULL,
    points_per_metadata_edit integer NOT NULL,
    points_per_stream_edit integer NOT NULL,
    points_for_rejection_penalty integer NOT NULL,
    contributor_threshold integer NOT NULL,
    trusted_threshold integer NOT NULL,
    expert_threshold integer NOT NULL,
    allow_auto_approval boolean NOT NULL,
    require_reason_for_edits boolean NOT NULL,
    max_pending_suggestions_per_user integer NOT NULL,
    broken_report_threshold integer DEFAULT 3 NOT NULL,
    auto_block_on_broken_reports boolean DEFAULT false NOT NULL
);


-- Name: contributions; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.contributions (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id character varying NOT NULL,
    user_id integer,
    contribution_type character varying NOT NULL,
    target_id character varying,
    data json NOT NULL,
    status public.contributionstatus NOT NULL,
    reviewed_by character varying,
    reviewed_at timestamp with time zone,
    review_notes character varying,
    admin_review_requested boolean DEFAULT false NOT NULL,
    admin_review_requested_by character varying,
    admin_review_requested_at timestamp with time zone,
    admin_review_reason text
);


-- Name: daily_stats; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.daily_stats (
    id integer NOT NULL,
    stat_date date NOT NULL,
    new_users integer NOT NULL,
    active_users integer NOT NULL,
    new_streams integer NOT NULL,
    total_playbacks integer NOT NULL,
    top_media json,
    top_streams json,
    created_at timestamp with time zone NOT NULL
);


-- Name: daily_stats_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.daily_stats_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: daily_stats_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.daily_stats_id_seq OWNED BY public.daily_stats.id;


-- Name: episode; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.episode (
    id integer NOT NULL,
    season_id integer NOT NULL,
    episode_number integer NOT NULL,
    title character varying NOT NULL,
    overview character varying,
    air_date date,
    runtime_minutes integer,
    imdb_id character varying,
    tmdb_id integer,
    tvdb_id integer,
    provider_id integer,
    source_provider_id integer,
    created_by_user_id integer,
    is_user_created boolean NOT NULL,
    is_user_addition boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


-- Name: episode_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.episode_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: episode_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.episode_id_seq OWNED BY public.episode.id;


-- Name: episode_image; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.episode_image (
    id integer NOT NULL,
    episode_id integer NOT NULL,
    provider_id integer NOT NULL,
    image_type character varying NOT NULL,
    url character varying NOT NULL,
    language character varying,
    aspect_ratio double precision,
    width integer,
    height integer,
    is_primary boolean NOT NULL
);


-- Name: episode_image_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.episode_image_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: episode_image_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.episode_image_id_seq OWNED BY public.episode_image.id;


-- Name: episode_suggestions; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.episode_suggestions (
    id character varying NOT NULL,
    user_id integer NOT NULL,
    episode_id integer NOT NULL,
    field_name character varying NOT NULL,
    current_value character varying,
    suggested_value character varying NOT NULL,
    reason character varying,
    status character varying DEFAULT 'pending'::character varying NOT NULL,
    reviewed_by character varying,
    reviewed_at timestamp with time zone,
    review_notes character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


-- Name: external_link_stream; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.external_link_stream (
    id integer NOT NULL,
    stream_id integer NOT NULL,
    url character varying NOT NULL,
    service_name character varying NOT NULL,
    service_icon_url character varying,
    requires_subscription boolean NOT NULL,
    region character varying,
    behavior_hints json
);


-- Name: external_link_stream_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.external_link_stream_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: external_link_stream_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.external_link_stream_id_seq OWNED BY public.external_link_stream.id;


-- Name: file_media_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.file_media_link (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    file_id integer NOT NULL,
    media_id integer NOT NULL,
    season_number integer,
    episode_number integer,
    episode_end integer,
    is_primary boolean NOT NULL,
    confidence double precision NOT NULL,
    link_source public.linksource NOT NULL,
    debrid_service character varying
);


-- Name: file_media_link_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.file_media_link_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: file_media_link_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.file_media_link_id_seq OWNED BY public.file_media_link.id;


-- Name: genre; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.genre (
    id integer NOT NULL,
    name character varying NOT NULL
);


-- Name: genre_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.genre_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: genre_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.genre_id_seq OWNED BY public.genre.id;


-- Name: hdr_format; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.hdr_format (
    id integer NOT NULL,
    name character varying NOT NULL
);


-- Name: hdr_format_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.hdr_format_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: hdr_format_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.hdr_format_id_seq OWNED BY public.hdr_format.id;


-- Name: http_stream; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.http_stream (
    id integer NOT NULL,
    stream_id integer NOT NULL,
    url character varying NOT NULL,
    format character varying,
    size bigint,
    bitrate_kbps integer,
    expires_at timestamp with time zone,
    behavior_hints jsonb,
    drm_key_id character varying,
    drm_key character varying,
    extractor_name character varying
);


-- Name: http_stream_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.http_stream_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: http_stream_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.http_stream_id_seq OWNED BY public.http_stream.id;


-- Name: iptv_source; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.iptv_source (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    user_id integer NOT NULL,
    source_type public.iptvsourcetype NOT NULL,
    name character varying NOT NULL,
    m3u_url character varying,
    server_url character varying,
    encrypted_credentials character varying,
    is_public boolean NOT NULL,
    import_live boolean NOT NULL,
    import_vod boolean NOT NULL,
    import_series boolean NOT NULL,
    live_category_ids json,
    vod_category_ids json,
    series_category_ids json,
    last_synced_at timestamp with time zone,
    last_sync_stats json,
    is_active boolean NOT NULL
);


-- Name: iptv_source_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.iptv_source_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: iptv_source_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.iptv_source_id_seq OWNED BY public.iptv_source.id;


-- Name: keyword; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.keyword (
    id integer NOT NULL,
    name character varying NOT NULL
);


-- Name: keyword_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.keyword_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: keyword_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.keyword_id_seq OWNED BY public.keyword.id;


-- Name: language; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.language (
    id integer NOT NULL,
    name character varying NOT NULL
);


-- Name: language_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.language_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: language_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.language_id_seq OWNED BY public.language.id;


-- Name: media; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    type public.mediatype NOT NULL,
    title character varying NOT NULL,
    original_title character varying,
    year integer,
    release_date date,
    end_date date,
    status character varying,
    runtime_minutes integer,
    description character varying,
    tagline character varying,
    adult boolean NOT NULL,
    original_language character varying,
    popularity double precision,
    website character varying,
    primary_provider_id integer,
    created_by_user_id integer,
    is_user_created boolean NOT NULL,
    is_public boolean NOT NULL,
    user_original_title character varying,
    last_refreshed_by_user_id integer,
    last_refreshed_at timestamp with time zone,
    migrated_from_id character varying,
    migrated_by_user_id integer,
    migrated_at timestamp with time zone,
    nudity_status public.nuditystatus NOT NULL,
    is_blocked boolean NOT NULL,
    blocked_at timestamp with time zone,
    blocked_by_user_id integer,
    block_reason character varying(500),
    last_scraped_at timestamp with time zone,
    last_scraped_by_user_id integer,
    total_streams integer NOT NULL,
    last_stream_added timestamp with time zone,
    title_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, (title)::text)) STORED NOT NULL,
    is_add_title_to_poster boolean DEFAULT false NOT NULL
);


-- Name: media_cast; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_cast (
    id integer NOT NULL,
    media_id integer NOT NULL,
    person_id integer NOT NULL,
    "character" character varying,
    display_order integer NOT NULL
);


-- Name: media_cast_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.media_cast_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: media_cast_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.media_cast_id_seq OWNED BY public.media_cast.id;


-- Name: media_catalog_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_catalog_link (
    media_id integer NOT NULL,
    catalog_id integer NOT NULL
);


-- Name: media_crew; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_crew (
    id integer NOT NULL,
    media_id integer NOT NULL,
    person_id integer NOT NULL,
    department character varying,
    job character varying
);


-- Name: media_crew_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.media_crew_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: media_crew_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.media_crew_id_seq OWNED BY public.media_crew.id;


-- Name: media_external_id; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_external_id (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    media_id integer NOT NULL,
    provider character varying NOT NULL,
    external_id character varying NOT NULL
);


-- Name: media_external_id_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.media_external_id_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: media_external_id_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.media_external_id_id_seq OWNED BY public.media_external_id.id;


-- Name: media_genre_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_genre_link (
    media_id integer NOT NULL,
    genre_id integer NOT NULL
);


-- Name: media_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.media_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: media_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.media_id_seq OWNED BY public.media.id;


-- Name: media_image; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_image (
    id integer NOT NULL,
    media_id integer NOT NULL,
    provider_id integer NOT NULL,
    image_type character varying NOT NULL,
    url character varying NOT NULL,
    language character varying,
    aspect_ratio double precision,
    width integer,
    height integer,
    vote_average double precision,
    vote_count integer,
    is_primary boolean NOT NULL,
    display_order integer NOT NULL
);


-- Name: media_image_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.media_image_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: media_image_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.media_image_id_seq OWNED BY public.media_image.id;


-- Name: media_keyword_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_keyword_link (
    media_id integer NOT NULL,
    keyword_id integer NOT NULL
);


-- Name: media_parental_certificate_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_parental_certificate_link (
    media_id integer NOT NULL,
    certificate_id integer NOT NULL
);


-- Name: media_production_company_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_production_company_link (
    media_id integer NOT NULL,
    company_id integer NOT NULL
);


-- Name: media_rating; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_rating (
    id integer NOT NULL,
    media_id integer NOT NULL,
    rating_provider_id integer NOT NULL,
    rating double precision NOT NULL,
    rating_raw double precision,
    vote_count integer,
    rating_type character varying,
    certification character varying,
    fetched_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


-- Name: media_rating_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.media_rating_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: media_rating_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.media_rating_id_seq OWNED BY public.media_rating.id;


-- Name: media_review; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_review (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    media_id integer NOT NULL,
    provider_id integer,
    user_id integer,
    author character varying,
    author_avatar character varying,
    content character varying NOT NULL,
    rating double precision,
    url character varying,
    published_at timestamp with time zone
);


-- Name: media_review_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.media_review_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: media_review_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.media_review_id_seq OWNED BY public.media_review.id;


-- Name: media_trailer; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.media_trailer (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    media_id integer NOT NULL,
    video_key character varying NOT NULL,
    site character varying NOT NULL,
    name character varying,
    trailer_type character varying NOT NULL,
    language character varying,
    country character varying,
    is_official boolean NOT NULL,
    is_primary boolean NOT NULL,
    size integer
);


-- Name: media_trailer_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.media_trailer_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: media_trailer_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.media_trailer_id_seq OWNED BY public.media_trailer.id;


-- Name: mediafusion_rating; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.mediafusion_rating (
    id integer NOT NULL,
    media_id integer NOT NULL,
    average_rating double precision NOT NULL,
    total_votes integer NOT NULL,
    upvotes integer NOT NULL,
    downvotes integer NOT NULL,
    five_star_count integer NOT NULL,
    four_star_count integer NOT NULL,
    three_star_count integer NOT NULL,
    two_star_count integer NOT NULL,
    one_star_count integer NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


-- Name: mediafusion_rating_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.mediafusion_rating_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: mediafusion_rating_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.mediafusion_rating_id_seq OWNED BY public.mediafusion_rating.id;


-- Name: metadata_provider; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.metadata_provider (
    id integer NOT NULL,
    name character varying NOT NULL,
    display_name character varying NOT NULL,
    api_base_url character varying,
    is_external boolean NOT NULL,
    is_active boolean NOT NULL,
    priority integer NOT NULL,
    default_priority integer NOT NULL,
    created_at timestamp with time zone NOT NULL
);


-- Name: metadata_provider_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.metadata_provider_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: metadata_provider_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.metadata_provider_id_seq OWNED BY public.metadata_provider.id;


-- Name: metadata_suggestions; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.metadata_suggestions (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id character varying NOT NULL,
    user_id integer NOT NULL,
    media_id integer NOT NULL,
    field_name character varying NOT NULL,
    current_value character varying,
    suggested_value character varying NOT NULL,
    reason character varying,
    status character varying NOT NULL,
    reviewed_by character varying,
    reviewed_at timestamp with time zone,
    review_notes character varying
);


-- Name: metadata_votes; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.metadata_votes (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id character varying NOT NULL,
    user_id integer NOT NULL,
    media_id integer NOT NULL,
    vote_type character varying NOT NULL,
    vote integer
);


-- Name: movie_metadata; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.movie_metadata (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    media_id integer NOT NULL,
    budget integer,
    revenue integer,
    mpaa_rating character varying
);


-- Name: movie_metadata_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.movie_metadata_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: movie_metadata_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.movie_metadata_id_seq OWNED BY public.movie_metadata.id;


-- Name: parental_certificate; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.parental_certificate (
    id integer NOT NULL,
    name character varying NOT NULL
);


-- Name: parental_certificate_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.parental_certificate_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: parental_certificate_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.parental_certificate_id_seq OWNED BY public.parental_certificate.id;


-- Name: person; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.person (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    tmdb_id integer,
    imdb_id character varying,
    name character varying NOT NULL,
    profile_url character varying,
    known_for_department character varying,
    birthday date,
    deathday date,
    biography character varying,
    place_of_birth character varying,
    popularity double precision,
    provider_id integer
);


-- Name: person_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.person_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: person_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.person_id_seq OWNED BY public.person.id;


-- Name: playback_tracking; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.playback_tracking (
    id integer NOT NULL,
    user_id integer,
    profile_id integer,
    stream_id integer NOT NULL,
    media_id integer NOT NULL,
    season integer,
    episode integer,
    provider_name character varying,
    provider_service character varying,
    first_played_at timestamp with time zone NOT NULL,
    last_played_at timestamp with time zone NOT NULL,
    play_count integer NOT NULL
);


-- Name: playback_tracking_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.playback_tracking_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: playback_tracking_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.playback_tracking_id_seq OWNED BY public.playback_tracking.id;


-- Name: production_company; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.production_company (
    id integer NOT NULL,
    name character varying NOT NULL,
    logo_url character varying,
    origin_country character varying,
    tmdb_id integer
);


-- Name: production_company_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.production_company_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: production_company_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.production_company_id_seq OWNED BY public.production_company.id;


-- Name: profile_integration; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.profile_integration (
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now(),
    id integer NOT NULL,
    profile_id integer NOT NULL,
    platform public.integrationtype NOT NULL,
    encrypted_credentials character varying,
    is_enabled boolean DEFAULT true NOT NULL,
    sync_direction character varying DEFAULT 'two_way'::character varying NOT NULL,
    scrobble_enabled boolean DEFAULT true NOT NULL,
    settings json DEFAULT '{}'::json NOT NULL,
    last_sync_at timestamp with time zone,
    last_sync_status character varying,
    last_sync_error character varying,
    sync_cursor json DEFAULT '{}'::json NOT NULL,
    last_sync_stats json DEFAULT '{}'::json NOT NULL
);


-- Name: profile_integration_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.profile_integration_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: profile_integration_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.profile_integration_id_seq OWNED BY public.profile_integration.id;


-- Name: provider_metadata; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.provider_metadata (
    id integer NOT NULL,
    media_id integer NOT NULL,
    provider_id integer NOT NULL,
    provider_content_id character varying NOT NULL,
    title character varying,
    original_title character varying,
    description character varying,
    tagline character varying,
    release_date timestamp with time zone,
    runtime integer,
    popularity double precision,
    priority integer NOT NULL,
    is_canonical boolean NOT NULL,
    raw_data json,
    fetched_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone
);


-- Name: provider_metadata_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.provider_metadata_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: provider_metadata_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.provider_metadata_id_seq OWNED BY public.provider_metadata.id;


-- Name: rating_provider; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.rating_provider (
    id integer NOT NULL,
    name character varying NOT NULL,
    display_name character varying NOT NULL,
    icon_url character varying,
    max_rating double precision NOT NULL,
    is_percentage boolean NOT NULL,
    is_active boolean NOT NULL,
    display_order integer NOT NULL
);


-- Name: rating_provider_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.rating_provider_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: rating_provider_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.rating_provider_id_seq OWNED BY public.rating_provider.id;


-- Name: rss_feed; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.rss_feed (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    uuid character varying NOT NULL,
    user_id integer NOT NULL,
    name character varying NOT NULL,
    url character varying NOT NULL,
    is_active boolean NOT NULL,
    is_public boolean NOT NULL,
    source character varying,
    torrent_type character varying NOT NULL,
    auto_detect_catalog boolean NOT NULL,
    parsing_patterns json,
    filters json,
    metrics json,
    last_scraped_at timestamp with time zone
);


-- Name: rss_feed_catalog_pattern; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.rss_feed_catalog_pattern (
    id integer NOT NULL,
    uuid character varying NOT NULL,
    rss_feed_id integer NOT NULL,
    name character varying,
    regex character varying NOT NULL,
    enabled boolean NOT NULL,
    case_sensitive boolean NOT NULL,
    target_catalogs json NOT NULL
);


-- Name: rss_feed_catalog_pattern_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.rss_feed_catalog_pattern_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: rss_feed_catalog_pattern_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.rss_feed_catalog_pattern_id_seq OWNED BY public.rss_feed_catalog_pattern.id;


-- Name: rss_feed_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.rss_feed_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: rss_feed_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.rss_feed_id_seq OWNED BY public.rss_feed.id;


-- Name: season; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.season (
    id integer NOT NULL,
    series_id integer NOT NULL,
    season_number integer NOT NULL,
    name character varying,
    overview character varying,
    air_date date,
    episode_count integer NOT NULL,
    provider_id integer
);


-- Name: season_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.season_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: season_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.season_id_seq OWNED BY public.season.id;


-- Name: series_metadata; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.series_metadata (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    media_id integer NOT NULL,
    total_seasons integer,
    total_episodes integer,
    network character varying
);


-- Name: series_metadata_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.series_metadata_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: series_metadata_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.series_metadata_id_seq OWNED BY public.series_metadata.id;


-- Name: stream; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.stream (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    stream_type public.streamtype NOT NULL,
    name character varying NOT NULL,
    source character varying NOT NULL,
    uploader character varying,
    uploader_user_id integer,
    release_group character varying,
    is_active boolean NOT NULL,
    is_blocked boolean NOT NULL,
    is_public boolean NOT NULL,
    playback_count integer NOT NULL,
    resolution character varying,
    codec character varying,
    quality character varying,
    bit_depth character varying,
    is_remastered boolean NOT NULL,
    is_upscaled boolean NOT NULL,
    is_proper boolean NOT NULL,
    is_repack boolean NOT NULL,
    is_extended boolean NOT NULL,
    is_complete boolean NOT NULL,
    is_dubbed boolean NOT NULL,
    is_subbed boolean NOT NULL
);


-- Name: stream_audio_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.stream_audio_link (
    stream_id integer NOT NULL,
    audio_format_id integer NOT NULL
);


-- Name: stream_channel_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.stream_channel_link (
    stream_id integer NOT NULL,
    channel_id integer NOT NULL
);


-- Name: stream_file; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.stream_file (
    id integer NOT NULL,
    stream_id integer NOT NULL,
    file_index integer,
    filename character varying NOT NULL,
    file_path character varying,
    size bigint,
    file_type public.filetype NOT NULL,
    is_archive boolean NOT NULL,
    archive_contents json
);


-- Name: stream_file_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.stream_file_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: stream_file_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.stream_file_id_seq OWNED BY public.stream_file.id;


-- Name: stream_hdr_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.stream_hdr_link (
    stream_id integer NOT NULL,
    hdr_format_id integer NOT NULL
);


-- Name: stream_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.stream_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: stream_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.stream_id_seq OWNED BY public.stream.id;


-- Name: stream_language_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.stream_language_link (
    stream_id integer NOT NULL,
    language_id integer NOT NULL,
    language_type character varying NOT NULL
);


-- Name: stream_media_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.stream_media_link (
    id integer NOT NULL,
    stream_id integer NOT NULL,
    media_id integer NOT NULL,
    linked_by_user_id integer,
    file_index integer,
    filename character varying,
    file_size bigint,
    is_primary boolean NOT NULL,
    is_verified boolean NOT NULL,
    confidence_score double precision,
    created_at timestamp with time zone NOT NULL
);


-- Name: stream_media_link_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.stream_media_link_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: stream_media_link_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.stream_media_link_id_seq OWNED BY public.stream_media_link.id;


-- Name: stream_suggestions; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.stream_suggestions (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id character varying NOT NULL,
    user_id integer NOT NULL,
    stream_id integer NOT NULL,
    suggestion_type character varying NOT NULL,
    current_value character varying,
    suggested_value character varying,
    reason character varying,
    status character varying NOT NULL,
    reviewed_by character varying,
    reviewed_at timestamp with time zone,
    review_notes character varying,
    issue_triage_status character varying,
    issue_triage_note text
);


-- Name: stream_votes; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.stream_votes (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id character varying NOT NULL,
    user_id integer NOT NULL,
    stream_id integer NOT NULL,
    vote_type character varying NOT NULL,
    quality_status character varying,
    comment character varying
);


-- Name: telegram_stream; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.telegram_stream (
    id integer NOT NULL,
    stream_id integer NOT NULL,
    chat_id character varying NOT NULL,
    chat_username character varying,
    message_id integer NOT NULL,
    file_id character varying,
    size bigint,
    mime_type character varying,
    file_name character varying,
    posted_at timestamp with time zone,
    file_unique_id character varying,
    backup_chat_id character varying,
    backup_message_id integer,
    document_id bigint
);


-- Name: telegram_stream_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.telegram_stream_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: telegram_stream_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.telegram_stream_id_seq OWNED BY public.telegram_stream.id;


-- Name: telegram_user_forward; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.telegram_user_forward (
    id integer NOT NULL,
    telegram_stream_id integer NOT NULL,
    user_id integer NOT NULL,
    telegram_user_id bigint NOT NULL,
    forwarded_chat_id character varying NOT NULL,
    forwarded_message_id integer NOT NULL,
    created_at timestamp with time zone NOT NULL
);


-- Name: telegram_user_forward_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.telegram_user_forward_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: telegram_user_forward_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.telegram_user_forward_id_seq OWNED BY public.telegram_user_forward.id;


-- Name: torrent_stream; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.torrent_stream (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    stream_id integer NOT NULL,
    info_hash character varying NOT NULL,
    total_size bigint NOT NULL,
    seeders integer,
    leechers integer,
    torrent_type public.torrenttype NOT NULL,
    uploaded_at timestamp with time zone,
    torrent_file bytea,
    file_count integer NOT NULL
);


-- Name: torrent_stream_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.torrent_stream_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: torrent_stream_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.torrent_stream_id_seq OWNED BY public.torrent_stream.id;


-- Name: torrent_tracker_link; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.torrent_tracker_link (
    torrent_id integer NOT NULL,
    tracker_id integer NOT NULL
);


-- Name: tracker; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.tracker (
    id integer NOT NULL,
    url character varying NOT NULL,
    status public.trackerstatus NOT NULL,
    success_count integer NOT NULL,
    failure_count integer NOT NULL,
    success_rate double precision NOT NULL,
    last_checked timestamp with time zone,
    last_success timestamp with time zone,
    created_at timestamp with time zone NOT NULL
);


-- Name: tracker_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.tracker_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: tracker_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.tracker_id_seq OWNED BY public.tracker.id;


-- Name: tv_metadata; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.tv_metadata (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    media_id integer NOT NULL,
    country character varying,
    tv_language character varying
);


-- Name: tv_metadata_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.tv_metadata_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: tv_metadata_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.tv_metadata_id_seq OWNED BY public.tv_metadata.id;


-- Name: usenet_stream; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.usenet_stream (
    id integer NOT NULL,
    stream_id integer NOT NULL,
    nzb_guid character varying NOT NULL,
    nzb_url character varying,
    size bigint NOT NULL,
    indexer character varying NOT NULL,
    group_name character varying,
    uploader character varying,
    files_count integer,
    parts_count integer,
    posted_at timestamp with time zone,
    is_passworded boolean NOT NULL
);


-- Name: usenet_stream_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.usenet_stream_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: usenet_stream_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.usenet_stream_id_seq OWNED BY public.usenet_stream.id;


-- Name: user_catalog; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.user_catalog (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    user_id integer NOT NULL,
    name character varying NOT NULL,
    description character varying,
    poster_url character varying,
    is_public boolean NOT NULL,
    is_listed boolean NOT NULL,
    share_code character varying NOT NULL,
    item_count integer NOT NULL,
    subscriber_count integer NOT NULL
);


-- Name: user_catalog_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.user_catalog_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: user_catalog_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.user_catalog_id_seq OWNED BY public.user_catalog.id;


-- Name: user_catalog_item; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.user_catalog_item (
    id integer NOT NULL,
    catalog_id integer NOT NULL,
    media_id integer NOT NULL,
    display_order integer NOT NULL,
    notes character varying,
    added_at timestamp with time zone NOT NULL
);


-- Name: user_catalog_item_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.user_catalog_item_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: user_catalog_item_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.user_catalog_item_id_seq OWNED BY public.user_catalog_item.id;


-- Name: user_catalog_subscription; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.user_catalog_subscription (
    id integer NOT NULL,
    user_id integer NOT NULL,
    catalog_id integer NOT NULL,
    subscribed_at timestamp with time zone NOT NULL
);


-- Name: user_catalog_subscription_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.user_catalog_subscription_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: user_catalog_subscription_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.user_catalog_subscription_id_seq OWNED BY public.user_catalog_subscription.id;


-- Name: user_library_item; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.user_library_item (
    id integer NOT NULL,
    user_id integer NOT NULL,
    media_id integer NOT NULL,
    catalog_type character varying NOT NULL,
    title_cached character varying NOT NULL,
    poster_cached character varying,
    added_at timestamp with time zone NOT NULL
);


-- Name: user_library_item_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.user_library_item_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: user_library_item_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.user_library_item_id_seq OWNED BY public.user_library_item.id;


-- Name: user_profiles; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.user_profiles (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    uuid character varying NOT NULL,
    user_id integer NOT NULL,
    name character varying NOT NULL,
    config json NOT NULL,
    encrypted_secrets character varying,
    is_default boolean NOT NULL
);


-- Name: user_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.user_profiles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: user_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.user_profiles_id_seq OWNED BY public.user_profiles.id;


-- Name: users; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.users (
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone,
    id integer NOT NULL,
    uuid character varying NOT NULL,
    email character varying NOT NULL,
    username character varying,
    password_hash character varying,
    role public.userrole NOT NULL,
    is_verified boolean NOT NULL,
    is_active boolean NOT NULL,
    last_login timestamp with time zone,
    contribution_points integer NOT NULL,
    metadata_edits_approved integer NOT NULL,
    stream_edits_approved integer NOT NULL,
    contribution_level character varying NOT NULL,
    telegram_user_id character varying,
    telegram_linked_at timestamp with time zone,
    contribute_anonymously boolean NOT NULL,
    uploads_restricted boolean NOT NULL
);


-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


-- Name: watch_history; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.watch_history (
    id integer NOT NULL,
    user_id integer NOT NULL,
    profile_id integer NOT NULL,
    media_id integer NOT NULL,
    title character varying NOT NULL,
    media_type character varying NOT NULL,
    season integer,
    episode integer,
    progress integer NOT NULL,
    duration integer,
    watched_at timestamp with time zone NOT NULL,
    action public.watchaction DEFAULT 'WATCHED'::public.watchaction NOT NULL,
    stream_info json DEFAULT '{}'::json NOT NULL,
    source public.historysource DEFAULT 'MEDIAFUSION'::public.historysource NOT NULL
);


-- Name: watch_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.watch_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: watch_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.watch_history_id_seq OWNED BY public.watch_history.id;


-- Name: youtube_stream; Type: TABLE; Schema: public; Owner: -

CREATE TABLE public.youtube_stream (
    id integer NOT NULL,
    stream_id integer NOT NULL,
    video_id character varying NOT NULL,
    channel_id character varying,
    channel_name character varying,
    duration_seconds integer,
    is_live boolean NOT NULL,
    is_premiere boolean NOT NULL,
    published_at timestamp with time zone,
    geo_restriction_type character varying,
    geo_restriction_countries jsonb
);


-- Name: youtube_stream_id_seq; Type: SEQUENCE; Schema: public; Owner: -

CREATE SEQUENCE public.youtube_stream_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- Name: youtube_stream_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -

ALTER SEQUENCE public.youtube_stream_id_seq OWNED BY public.youtube_stream.id;


-- Name: acestream_stream id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.acestream_stream ALTER COLUMN id SET DEFAULT nextval('public.acestream_stream_id_seq'::regclass);


-- Name: aka_title id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.aka_title ALTER COLUMN id SET DEFAULT nextval('public.aka_title_id_seq'::regclass);


-- Name: annotation_request_dismissal id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.annotation_request_dismissal ALTER COLUMN id SET DEFAULT nextval('public.annotation_request_dismissal_id_seq'::regclass);


-- Name: audio_channel id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.audio_channel ALTER COLUMN id SET DEFAULT nextval('public.audio_channel_id_seq'::regclass);


-- Name: audio_format id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.audio_format ALTER COLUMN id SET DEFAULT nextval('public.audio_format_id_seq'::regclass);


-- Name: catalog id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.catalog ALTER COLUMN id SET DEFAULT nextval('public.catalog_id_seq'::regclass);


-- Name: daily_stats id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_stats ALTER COLUMN id SET DEFAULT nextval('public.daily_stats_id_seq'::regclass);


-- Name: episode id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode ALTER COLUMN id SET DEFAULT nextval('public.episode_id_seq'::regclass);


-- Name: episode_image id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode_image ALTER COLUMN id SET DEFAULT nextval('public.episode_image_id_seq'::regclass);


-- Name: external_link_stream id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.external_link_stream ALTER COLUMN id SET DEFAULT nextval('public.external_link_stream_id_seq'::regclass);


-- Name: file_media_link id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.file_media_link ALTER COLUMN id SET DEFAULT nextval('public.file_media_link_id_seq'::regclass);


-- Name: genre id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.genre ALTER COLUMN id SET DEFAULT nextval('public.genre_id_seq'::regclass);


-- Name: hdr_format id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.hdr_format ALTER COLUMN id SET DEFAULT nextval('public.hdr_format_id_seq'::regclass);


-- Name: http_stream id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.http_stream ALTER COLUMN id SET DEFAULT nextval('public.http_stream_id_seq'::regclass);


-- Name: iptv_source id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.iptv_source ALTER COLUMN id SET DEFAULT nextval('public.iptv_source_id_seq'::regclass);


-- Name: keyword id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.keyword ALTER COLUMN id SET DEFAULT nextval('public.keyword_id_seq'::regclass);


-- Name: language id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.language ALTER COLUMN id SET DEFAULT nextval('public.language_id_seq'::regclass);


-- Name: media id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.media ALTER COLUMN id SET DEFAULT nextval('public.media_id_seq'::regclass);


-- Name: media_cast id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_cast ALTER COLUMN id SET DEFAULT nextval('public.media_cast_id_seq'::regclass);


-- Name: media_crew id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_crew ALTER COLUMN id SET DEFAULT nextval('public.media_crew_id_seq'::regclass);


-- Name: media_external_id id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_external_id ALTER COLUMN id SET DEFAULT nextval('public.media_external_id_id_seq'::regclass);


-- Name: media_image id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_image ALTER COLUMN id SET DEFAULT nextval('public.media_image_id_seq'::regclass);


-- Name: media_rating id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_rating ALTER COLUMN id SET DEFAULT nextval('public.media_rating_id_seq'::regclass);


-- Name: media_review id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_review ALTER COLUMN id SET DEFAULT nextval('public.media_review_id_seq'::regclass);


-- Name: media_trailer id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_trailer ALTER COLUMN id SET DEFAULT nextval('public.media_trailer_id_seq'::regclass);


-- Name: mediafusion_rating id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.mediafusion_rating ALTER COLUMN id SET DEFAULT nextval('public.mediafusion_rating_id_seq'::regclass);


-- Name: metadata_provider id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.metadata_provider ALTER COLUMN id SET DEFAULT nextval('public.metadata_provider_id_seq'::regclass);


-- Name: movie_metadata id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.movie_metadata ALTER COLUMN id SET DEFAULT nextval('public.movie_metadata_id_seq'::regclass);


-- Name: parental_certificate id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.parental_certificate ALTER COLUMN id SET DEFAULT nextval('public.parental_certificate_id_seq'::regclass);


-- Name: person id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.person ALTER COLUMN id SET DEFAULT nextval('public.person_id_seq'::regclass);


-- Name: playback_tracking id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.playback_tracking ALTER COLUMN id SET DEFAULT nextval('public.playback_tracking_id_seq'::regclass);


-- Name: production_company id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.production_company ALTER COLUMN id SET DEFAULT nextval('public.production_company_id_seq'::regclass);


-- Name: profile_integration id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.profile_integration ALTER COLUMN id SET DEFAULT nextval('public.profile_integration_id_seq'::regclass);


-- Name: provider_metadata id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.provider_metadata ALTER COLUMN id SET DEFAULT nextval('public.provider_metadata_id_seq'::regclass);


-- Name: rating_provider id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.rating_provider ALTER COLUMN id SET DEFAULT nextval('public.rating_provider_id_seq'::regclass);


-- Name: rss_feed id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.rss_feed ALTER COLUMN id SET DEFAULT nextval('public.rss_feed_id_seq'::regclass);


-- Name: rss_feed_catalog_pattern id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.rss_feed_catalog_pattern ALTER COLUMN id SET DEFAULT nextval('public.rss_feed_catalog_pattern_id_seq'::regclass);


-- Name: season id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.season ALTER COLUMN id SET DEFAULT nextval('public.season_id_seq'::regclass);


-- Name: series_metadata id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.series_metadata ALTER COLUMN id SET DEFAULT nextval('public.series_metadata_id_seq'::regclass);


-- Name: stream id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream ALTER COLUMN id SET DEFAULT nextval('public.stream_id_seq'::regclass);


-- Name: stream_file id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_file ALTER COLUMN id SET DEFAULT nextval('public.stream_file_id_seq'::regclass);


-- Name: stream_media_link id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_media_link ALTER COLUMN id SET DEFAULT nextval('public.stream_media_link_id_seq'::regclass);


-- Name: telegram_stream id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.telegram_stream ALTER COLUMN id SET DEFAULT nextval('public.telegram_stream_id_seq'::regclass);


-- Name: telegram_user_forward id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.telegram_user_forward ALTER COLUMN id SET DEFAULT nextval('public.telegram_user_forward_id_seq'::regclass);


-- Name: torrent_stream id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.torrent_stream ALTER COLUMN id SET DEFAULT nextval('public.torrent_stream_id_seq'::regclass);


-- Name: tracker id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.tracker ALTER COLUMN id SET DEFAULT nextval('public.tracker_id_seq'::regclass);


-- Name: tv_metadata id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.tv_metadata ALTER COLUMN id SET DEFAULT nextval('public.tv_metadata_id_seq'::regclass);


-- Name: usenet_stream id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.usenet_stream ALTER COLUMN id SET DEFAULT nextval('public.usenet_stream_id_seq'::regclass);


-- Name: user_catalog id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog ALTER COLUMN id SET DEFAULT nextval('public.user_catalog_id_seq'::regclass);


-- Name: user_catalog_item id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog_item ALTER COLUMN id SET DEFAULT nextval('public.user_catalog_item_id_seq'::regclass);


-- Name: user_catalog_subscription id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog_subscription ALTER COLUMN id SET DEFAULT nextval('public.user_catalog_subscription_id_seq'::regclass);


-- Name: user_library_item id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_library_item ALTER COLUMN id SET DEFAULT nextval('public.user_library_item_id_seq'::regclass);


-- Name: user_profiles id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_profiles ALTER COLUMN id SET DEFAULT nextval('public.user_profiles_id_seq'::regclass);


-- Name: users id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


-- Name: watch_history id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.watch_history ALTER COLUMN id SET DEFAULT nextval('public.watch_history_id_seq'::regclass);


-- Name: youtube_stream id; Type: DEFAULT; Schema: public; Owner: -

ALTER TABLE ONLY public.youtube_stream ALTER COLUMN id SET DEFAULT nextval('public.youtube_stream_id_seq'::regclass);


-- Name: acestream_stream acestream_stream_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.acestream_stream
    ADD CONSTRAINT acestream_stream_pkey PRIMARY KEY (id);


-- Name: acestream_stream acestream_stream_stream_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.acestream_stream
    ADD CONSTRAINT acestream_stream_stream_id_key UNIQUE (stream_id);


-- Name: aka_title aka_title_media_id_title_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.aka_title
    ADD CONSTRAINT aka_title_media_id_title_key UNIQUE (media_id, title);


-- Name: aka_title aka_title_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.aka_title
    ADD CONSTRAINT aka_title_pkey PRIMARY KEY (id);


-- Name: annotation_request_dismissal annotation_request_dismissal_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.annotation_request_dismissal
    ADD CONSTRAINT annotation_request_dismissal_pkey PRIMARY KEY (id);


-- Name: annotation_request_dismissal annotation_request_dismissal_stream_id_media_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.annotation_request_dismissal
    ADD CONSTRAINT annotation_request_dismissal_stream_id_media_id_key UNIQUE (stream_id, media_id);


-- Name: audio_channel audio_channel_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.audio_channel
    ADD CONSTRAINT audio_channel_pkey PRIMARY KEY (id);


-- Name: audio_format audio_format_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.audio_format
    ADD CONSTRAINT audio_format_pkey PRIMARY KEY (id);


-- Name: catalog catalog_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.catalog
    ADD CONSTRAINT catalog_pkey PRIMARY KEY (id);


-- Name: contribution_settings contribution_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.contribution_settings
    ADD CONSTRAINT contribution_settings_pkey PRIMARY KEY (id);


-- Name: contributions contributions_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.contributions
    ADD CONSTRAINT contributions_pkey PRIMARY KEY (id);


-- Name: daily_stats daily_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.daily_stats
    ADD CONSTRAINT daily_stats_pkey PRIMARY KEY (id);


-- Name: episode_image episode_image_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode_image
    ADD CONSTRAINT episode_image_pkey PRIMARY KEY (id);


-- Name: episode episode_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode
    ADD CONSTRAINT episode_pkey PRIMARY KEY (id);


-- Name: episode episode_season_id_episode_number_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode
    ADD CONSTRAINT episode_season_id_episode_number_key UNIQUE (season_id, episode_number);


-- Name: episode_suggestions episode_suggestions_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode_suggestions
    ADD CONSTRAINT episode_suggestions_pkey PRIMARY KEY (id);


-- Name: external_link_stream external_link_stream_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.external_link_stream
    ADD CONSTRAINT external_link_stream_pkey PRIMARY KEY (id);


-- Name: external_link_stream external_link_stream_stream_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.external_link_stream
    ADD CONSTRAINT external_link_stream_stream_id_key UNIQUE (stream_id);


-- Name: file_media_link file_media_link_file_id_media_id_season_number_episode_numb_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.file_media_link
    ADD CONSTRAINT file_media_link_file_id_media_id_season_number_episode_numb_key UNIQUE (file_id, media_id, season_number, episode_number);


-- Name: file_media_link file_media_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.file_media_link
    ADD CONSTRAINT file_media_link_pkey PRIMARY KEY (id);


-- Name: genre genre_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.genre
    ADD CONSTRAINT genre_pkey PRIMARY KEY (id);


-- Name: hdr_format hdr_format_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.hdr_format
    ADD CONSTRAINT hdr_format_pkey PRIMARY KEY (id);


-- Name: http_stream http_stream_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.http_stream
    ADD CONSTRAINT http_stream_pkey PRIMARY KEY (id);


-- Name: http_stream http_stream_stream_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.http_stream
    ADD CONSTRAINT http_stream_stream_id_key UNIQUE (stream_id);


-- Name: iptv_source iptv_source_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.iptv_source
    ADD CONSTRAINT iptv_source_pkey PRIMARY KEY (id);


-- Name: keyword keyword_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.keyword
    ADD CONSTRAINT keyword_pkey PRIMARY KEY (id);


-- Name: language language_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.language
    ADD CONSTRAINT language_pkey PRIMARY KEY (id);


-- Name: media_cast media_cast_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_cast
    ADD CONSTRAINT media_cast_pkey PRIMARY KEY (id);


-- Name: media_catalog_link media_catalog_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_catalog_link
    ADD CONSTRAINT media_catalog_link_pkey PRIMARY KEY (media_id, catalog_id);


-- Name: media_crew media_crew_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_crew
    ADD CONSTRAINT media_crew_pkey PRIMARY KEY (id);


-- Name: media_external_id media_external_id_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_external_id
    ADD CONSTRAINT media_external_id_pkey PRIMARY KEY (id);


-- Name: media_genre_link media_genre_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_genre_link
    ADD CONSTRAINT media_genre_link_pkey PRIMARY KEY (media_id, genre_id);


-- Name: media_image media_image_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_image
    ADD CONSTRAINT media_image_pkey PRIMARY KEY (id);


-- Name: media_keyword_link media_keyword_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_keyword_link
    ADD CONSTRAINT media_keyword_link_pkey PRIMARY KEY (media_id, keyword_id);


-- Name: media_parental_certificate_link media_parental_certificate_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_parental_certificate_link
    ADD CONSTRAINT media_parental_certificate_link_pkey PRIMARY KEY (media_id, certificate_id);


-- Name: media media_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media
    ADD CONSTRAINT media_pkey PRIMARY KEY (id);


-- Name: media_production_company_link media_production_company_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_production_company_link
    ADD CONSTRAINT media_production_company_link_pkey PRIMARY KEY (media_id, company_id);


-- Name: media_rating media_rating_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_rating
    ADD CONSTRAINT media_rating_pkey PRIMARY KEY (id);


-- Name: media_review media_review_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_review
    ADD CONSTRAINT media_review_pkey PRIMARY KEY (id);


-- Name: media_trailer media_trailer_media_id_video_key_site_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_trailer
    ADD CONSTRAINT media_trailer_media_id_video_key_site_key UNIQUE (media_id, video_key, site);


-- Name: media_trailer media_trailer_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_trailer
    ADD CONSTRAINT media_trailer_pkey PRIMARY KEY (id);


-- Name: mediafusion_rating mediafusion_rating_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.mediafusion_rating
    ADD CONSTRAINT mediafusion_rating_pkey PRIMARY KEY (id);


-- Name: metadata_provider metadata_provider_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.metadata_provider
    ADD CONSTRAINT metadata_provider_pkey PRIMARY KEY (id);


-- Name: metadata_suggestions metadata_suggestions_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.metadata_suggestions
    ADD CONSTRAINT metadata_suggestions_pkey PRIMARY KEY (id);


-- Name: metadata_votes metadata_votes_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.metadata_votes
    ADD CONSTRAINT metadata_votes_pkey PRIMARY KEY (id);


-- Name: movie_metadata movie_metadata_media_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.movie_metadata
    ADD CONSTRAINT movie_metadata_media_id_key UNIQUE (media_id);


-- Name: movie_metadata movie_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.movie_metadata
    ADD CONSTRAINT movie_metadata_pkey PRIMARY KEY (id);


-- Name: parental_certificate parental_certificate_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.parental_certificate
    ADD CONSTRAINT parental_certificate_pkey PRIMARY KEY (id);


-- Name: person person_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.person
    ADD CONSTRAINT person_pkey PRIMARY KEY (id);


-- Name: playback_tracking playback_tracking_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.playback_tracking
    ADD CONSTRAINT playback_tracking_pkey PRIMARY KEY (id);


-- Name: production_company production_company_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.production_company
    ADD CONSTRAINT production_company_pkey PRIMARY KEY (id);


-- Name: production_company production_company_tmdb_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.production_company
    ADD CONSTRAINT production_company_tmdb_id_key UNIQUE (tmdb_id);


-- Name: profile_integration profile_integration_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.profile_integration
    ADD CONSTRAINT profile_integration_pkey PRIMARY KEY (id);


-- Name: provider_metadata provider_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.provider_metadata
    ADD CONSTRAINT provider_metadata_pkey PRIMARY KEY (id);


-- Name: rating_provider rating_provider_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.rating_provider
    ADD CONSTRAINT rating_provider_pkey PRIMARY KEY (id);


-- Name: rss_feed_catalog_pattern rss_feed_catalog_pattern_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.rss_feed_catalog_pattern
    ADD CONSTRAINT rss_feed_catalog_pattern_pkey PRIMARY KEY (id);


-- Name: rss_feed rss_feed_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.rss_feed
    ADD CONSTRAINT rss_feed_pkey PRIMARY KEY (id);


-- Name: season season_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.season
    ADD CONSTRAINT season_pkey PRIMARY KEY (id);


-- Name: season season_series_id_season_number_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.season
    ADD CONSTRAINT season_series_id_season_number_key UNIQUE (series_id, season_number);


-- Name: series_metadata series_metadata_media_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.series_metadata
    ADD CONSTRAINT series_metadata_media_id_key UNIQUE (media_id);


-- Name: series_metadata series_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.series_metadata
    ADD CONSTRAINT series_metadata_pkey PRIMARY KEY (id);


-- Name: stream_audio_link stream_audio_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_audio_link
    ADD CONSTRAINT stream_audio_link_pkey PRIMARY KEY (stream_id, audio_format_id);


-- Name: stream_channel_link stream_channel_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_channel_link
    ADD CONSTRAINT stream_channel_link_pkey PRIMARY KEY (stream_id, channel_id);


-- Name: stream_file stream_file_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_file
    ADD CONSTRAINT stream_file_pkey PRIMARY KEY (id);


-- Name: stream_file stream_file_stream_id_file_index_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_file
    ADD CONSTRAINT stream_file_stream_id_file_index_key UNIQUE (stream_id, file_index);


-- Name: stream_hdr_link stream_hdr_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_hdr_link
    ADD CONSTRAINT stream_hdr_link_pkey PRIMARY KEY (stream_id, hdr_format_id);


-- Name: stream_language_link stream_language_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_language_link
    ADD CONSTRAINT stream_language_link_pkey PRIMARY KEY (stream_id, language_id);


-- Name: stream_media_link stream_media_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_media_link
    ADD CONSTRAINT stream_media_link_pkey PRIMARY KEY (id);


-- Name: stream stream_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream
    ADD CONSTRAINT stream_pkey PRIMARY KEY (id);


-- Name: stream_suggestions stream_suggestions_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_suggestions
    ADD CONSTRAINT stream_suggestions_pkey PRIMARY KEY (id);


-- Name: stream_votes stream_votes_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_votes
    ADD CONSTRAINT stream_votes_pkey PRIMARY KEY (id);


-- Name: telegram_stream telegram_stream_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.telegram_stream
    ADD CONSTRAINT telegram_stream_pkey PRIMARY KEY (id);


-- Name: telegram_stream telegram_stream_stream_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.telegram_stream
    ADD CONSTRAINT telegram_stream_stream_id_key UNIQUE (stream_id);


-- Name: telegram_user_forward telegram_user_forward_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.telegram_user_forward
    ADD CONSTRAINT telegram_user_forward_pkey PRIMARY KEY (id);


-- Name: torrent_stream torrent_stream_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.torrent_stream
    ADD CONSTRAINT torrent_stream_pkey PRIMARY KEY (id);


-- Name: torrent_stream torrent_stream_stream_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.torrent_stream
    ADD CONSTRAINT torrent_stream_stream_id_key UNIQUE (stream_id);


-- Name: torrent_tracker_link torrent_tracker_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.torrent_tracker_link
    ADD CONSTRAINT torrent_tracker_link_pkey PRIMARY KEY (torrent_id, tracker_id);


-- Name: tracker tracker_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.tracker
    ADD CONSTRAINT tracker_pkey PRIMARY KEY (id);


-- Name: tv_metadata tv_metadata_media_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.tv_metadata
    ADD CONSTRAINT tv_metadata_media_id_key UNIQUE (media_id);


-- Name: tv_metadata tv_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.tv_metadata
    ADD CONSTRAINT tv_metadata_pkey PRIMARY KEY (id);


-- Name: episode_image uq_episode_image; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode_image
    ADD CONSTRAINT uq_episode_image UNIQUE (episode_id, provider_id, image_type, url);


-- Name: media_image uq_media_image; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_image
    ADD CONSTRAINT uq_media_image UNIQUE (media_id, provider_id, image_type, url);


-- Name: media_rating uq_media_rating; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_rating
    ADD CONSTRAINT uq_media_rating UNIQUE (media_id, rating_provider_id, rating_type);


-- Name: media_external_id uq_provider_external_id; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_external_id
    ADD CONSTRAINT uq_provider_external_id UNIQUE (provider, external_id);


-- Name: rss_feed uq_rss_feed_user_url; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.rss_feed
    ADD CONSTRAINT uq_rss_feed_user_url UNIQUE (user_id, url);


-- Name: telegram_user_forward uq_tg_forward_stream_user; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.telegram_user_forward
    ADD CONSTRAINT uq_tg_forward_stream_user UNIQUE (telegram_stream_id, user_id);


-- Name: metadata_votes uq_user_metadata_vote; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.metadata_votes
    ADD CONSTRAINT uq_user_metadata_vote UNIQUE (user_id, media_id);


-- Name: stream_votes uq_user_stream_vote; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_votes
    ADD CONSTRAINT uq_user_stream_vote UNIQUE (user_id, stream_id);


-- Name: usenet_stream usenet_stream_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.usenet_stream
    ADD CONSTRAINT usenet_stream_pkey PRIMARY KEY (id);


-- Name: usenet_stream usenet_stream_stream_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.usenet_stream
    ADD CONSTRAINT usenet_stream_stream_id_key UNIQUE (stream_id);


-- Name: user_catalog_item user_catalog_item_catalog_id_media_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog_item
    ADD CONSTRAINT user_catalog_item_catalog_id_media_id_key UNIQUE (catalog_id, media_id);


-- Name: user_catalog_item user_catalog_item_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog_item
    ADD CONSTRAINT user_catalog_item_pkey PRIMARY KEY (id);


-- Name: user_catalog user_catalog_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog
    ADD CONSTRAINT user_catalog_pkey PRIMARY KEY (id);


-- Name: user_catalog_subscription user_catalog_subscription_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog_subscription
    ADD CONSTRAINT user_catalog_subscription_pkey PRIMARY KEY (id);


-- Name: user_catalog_subscription user_catalog_subscription_user_id_catalog_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog_subscription
    ADD CONSTRAINT user_catalog_subscription_user_id_catalog_id_key UNIQUE (user_id, catalog_id);


-- Name: user_library_item user_library_item_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_library_item
    ADD CONSTRAINT user_library_item_pkey PRIMARY KEY (id);


-- Name: user_library_item user_library_item_user_id_media_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_library_item
    ADD CONSTRAINT user_library_item_user_id_media_id_key UNIQUE (user_id, media_id);


-- Name: user_profiles user_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_pkey PRIMARY KEY (id);


-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


-- Name: watch_history watch_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.watch_history
    ADD CONSTRAINT watch_history_pkey PRIMARY KEY (id);


-- Name: youtube_stream youtube_stream_pkey; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.youtube_stream
    ADD CONSTRAINT youtube_stream_pkey PRIMARY KEY (id);


-- Name: youtube_stream youtube_stream_stream_id_key; Type: CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.youtube_stream
    ADD CONSTRAINT youtube_stream_stream_id_key UNIQUE (stream_id);


-- Name: idx_aka_title_fts; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_aka_title_fts ON public.aka_title USING gin (title_tsv);


-- Name: idx_aka_title_trgm; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_aka_title_trgm ON public.aka_title USING gin (title public.gin_trgm_ops);


-- Name: idx_annotation_dismissal_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_annotation_dismissal_media ON public.annotation_request_dismissal USING btree (media_id);


-- Name: idx_annotation_dismissal_stream; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_annotation_dismissal_stream ON public.annotation_request_dismissal USING btree (stream_id);


-- Name: idx_catalog_item_catalog; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_catalog_item_catalog ON public.user_catalog_item USING btree (catalog_id);


-- Name: idx_catalog_item_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_catalog_item_media ON public.user_catalog_item USING btree (media_id);


-- Name: idx_catalog_item_order; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_catalog_item_order ON public.user_catalog_item USING btree (catalog_id, display_order);


-- Name: idx_contribution_admin_review_requested; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_contribution_admin_review_requested ON public.contributions USING btree (admin_review_requested);


-- Name: idx_contribution_status; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_contribution_status ON public.contributions USING btree (status);


-- Name: idx_contribution_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_contribution_type ON public.contributions USING btree (contribution_type);


-- Name: idx_contribution_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_contribution_user ON public.contributions USING btree (user_id);


-- Name: idx_episode_external_ids; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_episode_external_ids ON public.episode USING btree (imdb_id, tmdb_id, tvdb_id);


-- Name: idx_episode_image_episode; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_episode_image_episode ON public.episode_image USING btree (episode_id);


-- Name: idx_episode_user_addition; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_episode_user_addition ON public.episode USING btree (is_user_addition) WHERE (is_user_addition = true);


-- Name: idx_episode_user_created; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_episode_user_created ON public.episode USING btree (is_user_created) WHERE (is_user_created = true);


-- Name: idx_file_media_link_episode; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_file_media_link_episode ON public.file_media_link USING btree (media_id, season_number, episode_number);


-- Name: idx_file_media_link_file; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_file_media_link_file ON public.file_media_link USING btree (file_id);


-- Name: idx_file_media_link_file_episode; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_file_media_link_file_episode ON public.file_media_link USING btree (file_id, episode_number);


-- Name: idx_file_media_link_file_media_episode; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_file_media_link_file_media_episode ON public.file_media_link USING btree (file_id, media_id, episode_number);


-- Name: idx_file_media_link_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_file_media_link_media ON public.file_media_link USING btree (media_id);


-- Name: idx_integration_profile_platform; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX idx_integration_profile_platform ON public.profile_integration USING btree (profile_id, platform);


-- Name: idx_iptv_source_active; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_iptv_source_active ON public.iptv_source USING btree (is_active);


-- Name: idx_iptv_source_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_iptv_source_type ON public.iptv_source USING btree (source_type);


-- Name: idx_iptv_source_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_iptv_source_user ON public.iptv_source USING btree (user_id);


-- Name: idx_library_added; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_library_added ON public.user_library_item USING btree (added_at);


-- Name: idx_library_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_library_media ON public.user_library_item USING btree (media_id);


-- Name: idx_library_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_library_type ON public.user_library_item USING btree (catalog_type);


-- Name: idx_library_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_library_user ON public.user_library_item USING btree (user_id);


-- Name: idx_media_blocked; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_blocked ON public.media USING btree (is_blocked) WHERE (is_blocked = true);


-- Name: idx_media_cast_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_cast_media ON public.media_cast USING btree (media_id);


-- Name: idx_media_cast_order; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_cast_order ON public.media_cast USING btree (media_id, display_order);


-- Name: idx_media_cast_person; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_cast_person ON public.media_cast USING btree (person_id);


-- Name: idx_media_created_by_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_created_by_user ON public.media USING btree (created_by_user_id);


-- Name: idx_media_crew_department; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_crew_department ON public.media_crew USING btree (department);


-- Name: idx_media_crew_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_crew_media ON public.media_crew USING btree (media_id);


-- Name: idx_media_crew_person; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_crew_person ON public.media_crew USING btree (person_id);


-- Name: idx_media_external_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_external_media ON public.media_external_id USING btree (media_id);


-- Name: idx_media_external_provider_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_external_provider_id ON public.media_external_id USING btree (provider, external_id);


-- Name: idx_media_last_stream_added; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_last_stream_added ON public.media USING btree (last_stream_added);


-- Name: idx_media_last_stream_added_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_last_stream_added_type ON public.media USING btree (last_stream_added, type);


-- Name: idx_media_review_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_review_media ON public.media_review USING btree (media_id);


-- Name: idx_media_review_provider; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_review_provider ON public.media_review USING btree (provider_id);


-- Name: idx_media_review_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_review_user ON public.media_review USING btree (user_id);


-- Name: idx_media_title_fts; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_title_fts ON public.media USING gin (title_tsv);


-- Name: idx_media_title_trgm; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_title_trgm ON public.media USING gin (title public.gin_trgm_ops);


-- Name: idx_media_trailer_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_trailer_media ON public.media_trailer USING btree (media_id);


-- Name: idx_media_trailer_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_trailer_type ON public.media_trailer USING btree (trailer_type);


-- Name: idx_media_type_title; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_type_title ON public.media USING btree (type, title);


-- Name: idx_media_user_created; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_media_user_created ON public.media USING btree (is_user_created);


-- Name: idx_metadata_vote_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_metadata_vote_media ON public.metadata_votes USING btree (media_id);


-- Name: idx_metadata_vote_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_metadata_vote_user ON public.metadata_votes USING btree (user_id);


-- Name: idx_person_imdb; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_person_imdb ON public.person USING btree (imdb_id);


-- Name: idx_person_name; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_person_name ON public.person USING btree (name);


-- Name: idx_person_name_trgm; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_person_name_trgm ON public.person USING gin (name public.gin_trgm_ops);


-- Name: idx_person_tmdb; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_person_tmdb ON public.person USING btree (tmdb_id);


-- Name: idx_playback_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_playback_at ON public.playback_tracking USING btree (last_played_at);


-- Name: idx_playback_stream; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_playback_stream ON public.playback_tracking USING btree (stream_id);


-- Name: idx_playback_user_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_playback_user_media ON public.playback_tracking USING btree (user_id, media_id);


-- Name: idx_playback_user_stream; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_playback_user_stream ON public.playback_tracking USING btree (user_id, stream_id);


-- Name: idx_profile_user_default; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_profile_user_default ON public.user_profiles USING btree (user_id, is_default);


-- Name: idx_provider_metadata_canonical; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_provider_metadata_canonical ON public.provider_metadata USING btree (media_id) WHERE (is_canonical = true);


-- Name: idx_provider_metadata_media_provider; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_provider_metadata_media_provider ON public.provider_metadata USING btree (media_id, provider_id);


-- Name: idx_rss_feed_active; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_rss_feed_active ON public.rss_feed USING btree (is_active);


-- Name: idx_rss_feed_public; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_rss_feed_public ON public.rss_feed USING btree (is_public);


-- Name: idx_rss_feed_source; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_rss_feed_source ON public.rss_feed USING btree (source);


-- Name: idx_rss_feed_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_rss_feed_user ON public.rss_feed USING btree (user_id);


-- Name: idx_rss_pattern_feed; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_rss_pattern_feed ON public.rss_feed_catalog_pattern USING btree (rss_feed_id);


-- Name: idx_stream_active; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_active ON public.stream USING btree (is_active);


-- Name: idx_stream_active_blocked; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_active_blocked ON public.stream USING btree (is_active, is_blocked);


-- Name: idx_stream_active_blocked_created; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_active_blocked_created ON public.stream USING btree (is_active, is_blocked, created_at);


-- Name: idx_stream_blocked; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_blocked ON public.stream USING btree (is_blocked);


-- Name: idx_stream_file_stream; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_file_stream ON public.stream_file USING btree (stream_id);


-- Name: idx_stream_file_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_file_type ON public.stream_file USING btree (file_type);


-- Name: idx_stream_media_link_media_stream; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_media_link_media_stream ON public.stream_media_link USING btree (media_id, stream_id);


-- Name: idx_stream_media_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_media_media ON public.stream_media_link USING btree (media_id);


-- Name: idx_stream_media_primary; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_media_primary ON public.stream_media_link USING btree (stream_id, is_primary);


-- Name: idx_stream_media_stream; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_media_stream ON public.stream_media_link USING btree (stream_id);


-- Name: idx_stream_media_stream_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_media_stream_media ON public.stream_media_link USING btree (stream_id, media_id);


-- Name: idx_stream_media_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_media_user ON public.stream_media_link USING btree (linked_by_user_id);


-- Name: idx_stream_public; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_public ON public.stream USING btree (is_public);


-- Name: idx_stream_source; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_source ON public.stream USING btree (source);


-- Name: idx_stream_suggestion_status; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_suggestion_status ON public.stream_suggestions USING btree (status);


-- Name: idx_stream_suggestion_stream; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_suggestion_stream ON public.stream_suggestions USING btree (stream_id);


-- Name: idx_stream_suggestion_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_suggestion_type ON public.stream_suggestions USING btree (suggestion_type);


-- Name: idx_stream_suggestion_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_suggestion_user ON public.stream_suggestions USING btree (user_id);


-- Name: idx_stream_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_type ON public.stream USING btree (stream_type);


-- Name: idx_stream_uploader_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_uploader_user ON public.stream USING btree (uploader_user_id) WHERE (uploader_user_id IS NOT NULL);


-- Name: idx_stream_vote_stream; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_vote_stream ON public.stream_votes USING btree (stream_id);


-- Name: idx_stream_vote_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_vote_type ON public.stream_votes USING btree (vote_type);


-- Name: idx_stream_vote_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_stream_vote_user ON public.stream_votes USING btree (user_id);


-- Name: idx_subscription_catalog; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_subscription_catalog ON public.user_catalog_subscription USING btree (catalog_id);


-- Name: idx_subscription_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_subscription_user ON public.user_catalog_subscription USING btree (user_id);


-- Name: idx_suggestion_field; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_suggestion_field ON public.metadata_suggestions USING btree (field_name);


-- Name: idx_suggestion_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_suggestion_media ON public.metadata_suggestions USING btree (media_id);


-- Name: idx_suggestion_status; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_suggestion_status ON public.metadata_suggestions USING btree (status);


-- Name: idx_suggestion_user; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_suggestion_user ON public.metadata_suggestions USING btree (user_id);


-- Name: idx_telegram_chat_message; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_telegram_chat_message ON public.telegram_stream USING btree (chat_id, message_id);


-- Name: idx_telegram_document_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_telegram_document_id ON public.telegram_stream USING btree (document_id);


-- Name: idx_telegram_file_unique_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_telegram_file_unique_id ON public.telegram_stream USING btree (file_unique_id);


-- Name: idx_tg_forward_user_stream; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_tg_forward_user_stream ON public.telegram_user_forward USING btree (user_id, telegram_stream_id);


-- Name: idx_torrent_info_hash; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_torrent_info_hash ON public.torrent_stream USING btree (info_hash);


-- Name: idx_torrent_seeders; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_torrent_seeders ON public.torrent_stream USING btree (seeders);


-- Name: idx_usenet_guid; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_usenet_guid ON public.usenet_stream USING btree (nzb_guid);


-- Name: idx_user_catalog_listed; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_user_catalog_listed ON public.user_catalog USING btree (is_listed);


-- Name: idx_user_catalog_owner; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_user_catalog_owner ON public.user_catalog USING btree (user_id);


-- Name: idx_user_catalog_public; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_user_catalog_public ON public.user_catalog USING btree (is_public);


-- Name: idx_user_contribution_level; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_user_contribution_level ON public.users USING btree (contribution_level);


-- Name: idx_user_email; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_user_email ON public.users USING btree (email);


-- Name: idx_user_telegram_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_user_telegram_user_id ON public.users USING btree (telegram_user_id);


-- Name: idx_user_uploads_restricted; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_user_uploads_restricted ON public.users USING btree (uploads_restricted);


-- Name: idx_user_username; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_user_username ON public.users USING btree (username);


-- Name: idx_watch_profile_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_watch_profile_media ON public.watch_history USING btree (profile_id, media_id);


-- Name: idx_watch_user_media; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_watch_user_media ON public.watch_history USING btree (user_id, media_id);


-- Name: idx_watch_watched_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_watch_watched_at ON public.watch_history USING btree (watched_at);


-- Name: idx_youtube_video_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX idx_youtube_video_id ON public.youtube_stream USING btree (video_id);


-- Name: ix_acestream_stream_content_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_acestream_stream_content_id ON public.acestream_stream USING btree (content_id);


-- Name: ix_acestream_stream_info_hash; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_acestream_stream_info_hash ON public.acestream_stream USING btree (info_hash);


-- Name: ix_acestream_stream_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_acestream_stream_stream_id ON public.acestream_stream USING btree (stream_id);


-- Name: ix_aka_title_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_aka_title_media_id ON public.aka_title USING btree (media_id);


-- Name: ix_aka_title_title; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_aka_title_title ON public.aka_title USING btree (title);


-- Name: ix_audio_channel_name; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_audio_channel_name ON public.audio_channel USING btree (name);


-- Name: ix_audio_format_name; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_audio_format_name ON public.audio_format USING btree (name);


-- Name: ix_catalog_name; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_catalog_name ON public.catalog USING btree (name);


-- Name: ix_contributions_contribution_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_contributions_contribution_type ON public.contributions USING btree (contribution_type);


-- Name: ix_contributions_status; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_contributions_status ON public.contributions USING btree (status);


-- Name: ix_contributions_target_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_contributions_target_id ON public.contributions USING btree (target_id);


-- Name: ix_contributions_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_contributions_updated_at ON public.contributions USING btree (updated_at);


-- Name: ix_contributions_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_contributions_user_id ON public.contributions USING btree (user_id);


-- Name: ix_daily_stats_stat_date; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_daily_stats_stat_date ON public.daily_stats USING btree (stat_date);


-- Name: ix_episode_episode_number; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_episode_number ON public.episode USING btree (episode_number);


-- Name: ix_episode_image_episode_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_image_episode_id ON public.episode_image USING btree (episode_id);


-- Name: ix_episode_image_is_primary; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_image_is_primary ON public.episode_image USING btree (is_primary);


-- Name: ix_episode_imdb_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_imdb_id ON public.episode USING btree (imdb_id);


-- Name: ix_episode_is_user_addition; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_is_user_addition ON public.episode USING btree (is_user_addition);


-- Name: ix_episode_is_user_created; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_is_user_created ON public.episode USING btree (is_user_created);


-- Name: ix_episode_season_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_season_id ON public.episode USING btree (season_id);


-- Name: ix_episode_suggestions_episode_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_suggestions_episode_id ON public.episode_suggestions USING btree (episode_id);


-- Name: ix_episode_suggestions_status; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_suggestions_status ON public.episode_suggestions USING btree (status);


-- Name: ix_episode_suggestions_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_suggestions_updated_at ON public.episode_suggestions USING btree (updated_at);


-- Name: ix_episode_suggestions_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_suggestions_user_id ON public.episode_suggestions USING btree (user_id);


-- Name: ix_episode_tmdb_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_tmdb_id ON public.episode USING btree (tmdb_id);


-- Name: ix_episode_tvdb_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_episode_tvdb_id ON public.episode USING btree (tvdb_id);


-- Name: ix_external_link_stream_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_external_link_stream_stream_id ON public.external_link_stream USING btree (stream_id);


-- Name: ix_file_media_link_file_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_file_media_link_file_id ON public.file_media_link USING btree (file_id);


-- Name: ix_file_media_link_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_file_media_link_media_id ON public.file_media_link USING btree (media_id);


-- Name: ix_file_media_link_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_file_media_link_updated_at ON public.file_media_link USING btree (updated_at);


-- Name: ix_genre_name; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_genre_name ON public.genre USING btree (name);


-- Name: ix_hdr_format_name; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_hdr_format_name ON public.hdr_format USING btree (name);


-- Name: ix_http_stream_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_http_stream_stream_id ON public.http_stream USING btree (stream_id);


-- Name: ix_iptv_source_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_iptv_source_updated_at ON public.iptv_source USING btree (updated_at);


-- Name: ix_keyword_name; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_keyword_name ON public.keyword USING btree (name);


-- Name: ix_language_name; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_language_name ON public.language USING btree (name);


-- Name: ix_media_cast_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_cast_media_id ON public.media_cast USING btree (media_id);


-- Name: ix_media_cast_person_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_cast_person_id ON public.media_cast USING btree (person_id);


-- Name: ix_media_catalog_link_catalog_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_catalog_link_catalog_id ON public.media_catalog_link USING btree (catalog_id);


-- Name: ix_media_crew_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_crew_media_id ON public.media_crew USING btree (media_id);


-- Name: ix_media_crew_person_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_crew_person_id ON public.media_crew USING btree (person_id);


-- Name: ix_media_external_id_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_external_id_media_id ON public.media_external_id USING btree (media_id);


-- Name: ix_media_external_id_provider; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_external_id_provider ON public.media_external_id USING btree (provider);


-- Name: ix_media_external_id_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_external_id_updated_at ON public.media_external_id USING btree (updated_at);


-- Name: ix_media_genre_link_genre_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_genre_link_genre_id ON public.media_genre_link USING btree (genre_id);


-- Name: ix_media_image_image_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_image_image_type ON public.media_image USING btree (image_type);


-- Name: ix_media_image_is_primary; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_image_is_primary ON public.media_image USING btree (is_primary);


-- Name: ix_media_image_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_image_media_id ON public.media_image USING btree (media_id);


-- Name: ix_media_is_blocked; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_is_blocked ON public.media USING btree (is_blocked);


-- Name: ix_media_is_user_created; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_is_user_created ON public.media USING btree (is_user_created);


-- Name: ix_media_keyword_link_keyword_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_keyword_link_keyword_id ON public.media_keyword_link USING btree (keyword_id);


-- Name: ix_media_last_stream_added; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_last_stream_added ON public.media USING btree (last_stream_added);


-- Name: ix_media_nudity_status; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_nudity_status ON public.media USING btree (nudity_status);


-- Name: ix_media_parental_certificate_link_certificate_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_parental_certificate_link_certificate_id ON public.media_parental_certificate_link USING btree (certificate_id);


-- Name: ix_media_production_company_link_company_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_production_company_link_company_id ON public.media_production_company_link USING btree (company_id);


-- Name: ix_media_rating_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_rating_media_id ON public.media_rating USING btree (media_id);


-- Name: ix_media_review_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_review_media_id ON public.media_review USING btree (media_id);


-- Name: ix_media_review_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_review_updated_at ON public.media_review USING btree (updated_at);


-- Name: ix_media_total_streams; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_total_streams ON public.media USING btree (total_streams);


-- Name: ix_media_trailer_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_trailer_media_id ON public.media_trailer USING btree (media_id);


-- Name: ix_media_trailer_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_trailer_updated_at ON public.media_trailer USING btree (updated_at);


-- Name: ix_media_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_type ON public.media USING btree (type);


-- Name: ix_media_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_updated_at ON public.media USING btree (updated_at);


-- Name: ix_media_year; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_media_year ON public.media USING btree (year);


-- Name: ix_mediafusion_rating_media_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_mediafusion_rating_media_id ON public.mediafusion_rating USING btree (media_id);


-- Name: ix_metadata_provider_name; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_metadata_provider_name ON public.metadata_provider USING btree (name);


-- Name: ix_metadata_suggestions_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_metadata_suggestions_media_id ON public.metadata_suggestions USING btree (media_id);


-- Name: ix_metadata_suggestions_status; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_metadata_suggestions_status ON public.metadata_suggestions USING btree (status);


-- Name: ix_metadata_suggestions_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_metadata_suggestions_updated_at ON public.metadata_suggestions USING btree (updated_at);


-- Name: ix_metadata_suggestions_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_metadata_suggestions_user_id ON public.metadata_suggestions USING btree (user_id);


-- Name: ix_metadata_votes_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_metadata_votes_media_id ON public.metadata_votes USING btree (media_id);


-- Name: ix_metadata_votes_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_metadata_votes_updated_at ON public.metadata_votes USING btree (updated_at);


-- Name: ix_metadata_votes_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_metadata_votes_user_id ON public.metadata_votes USING btree (user_id);


-- Name: ix_movie_metadata_media_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_movie_metadata_media_id ON public.movie_metadata USING btree (media_id);


-- Name: ix_movie_metadata_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_movie_metadata_updated_at ON public.movie_metadata USING btree (updated_at);


-- Name: ix_parental_certificate_name; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_parental_certificate_name ON public.parental_certificate USING btree (name);


-- Name: ix_person_imdb_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_person_imdb_id ON public.person USING btree (imdb_id);


-- Name: ix_person_name; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_person_name ON public.person USING btree (name);


-- Name: ix_person_tmdb_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_person_tmdb_id ON public.person USING btree (tmdb_id);


-- Name: ix_person_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_person_updated_at ON public.person USING btree (updated_at);


-- Name: ix_playback_tracking_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_playback_tracking_media_id ON public.playback_tracking USING btree (media_id);


-- Name: ix_playback_tracking_profile_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_playback_tracking_profile_id ON public.playback_tracking USING btree (profile_id);


-- Name: ix_playback_tracking_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_playback_tracking_stream_id ON public.playback_tracking USING btree (stream_id);


-- Name: ix_playback_tracking_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_playback_tracking_user_id ON public.playback_tracking USING btree (user_id);


-- Name: ix_production_company_name; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_production_company_name ON public.production_company USING btree (name);


-- Name: ix_profile_integration_is_enabled; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_profile_integration_is_enabled ON public.profile_integration USING btree (is_enabled);


-- Name: ix_profile_integration_platform; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_profile_integration_platform ON public.profile_integration USING btree (platform);


-- Name: ix_profile_integration_profile_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_profile_integration_profile_id ON public.profile_integration USING btree (profile_id);


-- Name: ix_profile_integration_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_profile_integration_updated_at ON public.profile_integration USING btree (updated_at);


-- Name: ix_provider_metadata_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_provider_metadata_media_id ON public.provider_metadata USING btree (media_id);


-- Name: ix_rating_provider_name; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_rating_provider_name ON public.rating_provider USING btree (name);


-- Name: ix_rss_feed_catalog_pattern_rss_feed_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_rss_feed_catalog_pattern_rss_feed_id ON public.rss_feed_catalog_pattern USING btree (rss_feed_id);


-- Name: ix_rss_feed_catalog_pattern_uuid; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_rss_feed_catalog_pattern_uuid ON public.rss_feed_catalog_pattern USING btree (uuid);


-- Name: ix_rss_feed_is_active; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_rss_feed_is_active ON public.rss_feed USING btree (is_active);


-- Name: ix_rss_feed_is_public; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_rss_feed_is_public ON public.rss_feed USING btree (is_public);


-- Name: ix_rss_feed_name; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_rss_feed_name ON public.rss_feed USING btree (name);


-- Name: ix_rss_feed_source; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_rss_feed_source ON public.rss_feed USING btree (source);


-- Name: ix_rss_feed_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_rss_feed_updated_at ON public.rss_feed USING btree (updated_at);


-- Name: ix_rss_feed_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_rss_feed_user_id ON public.rss_feed USING btree (user_id);


-- Name: ix_rss_feed_uuid; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_rss_feed_uuid ON public.rss_feed USING btree (uuid);


-- Name: ix_season_season_number; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_season_season_number ON public.season USING btree (season_number);


-- Name: ix_season_series_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_season_series_id ON public.season USING btree (series_id);


-- Name: ix_series_metadata_media_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_series_metadata_media_id ON public.series_metadata USING btree (media_id);


-- Name: ix_series_metadata_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_series_metadata_updated_at ON public.series_metadata USING btree (updated_at);


-- Name: ix_stream_audio_link_audio_format_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_audio_link_audio_format_id ON public.stream_audio_link USING btree (audio_format_id);


-- Name: ix_stream_bit_depth; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_bit_depth ON public.stream USING btree (bit_depth);


-- Name: ix_stream_channel_link_channel_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_channel_link_channel_id ON public.stream_channel_link USING btree (channel_id);


-- Name: ix_stream_codec; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_codec ON public.stream USING btree (codec);


-- Name: ix_stream_file_file_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_file_file_type ON public.stream_file USING btree (file_type);


-- Name: ix_stream_file_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_file_stream_id ON public.stream_file USING btree (stream_id);


-- Name: ix_stream_hdr_link_hdr_format_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_hdr_link_hdr_format_id ON public.stream_hdr_link USING btree (hdr_format_id);


-- Name: ix_stream_language_link_language_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_language_link_language_id ON public.stream_language_link USING btree (language_id);


-- Name: ix_stream_quality; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_quality ON public.stream USING btree (quality);


-- Name: ix_stream_release_group; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_release_group ON public.stream USING btree (release_group);


-- Name: ix_stream_resolution; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_resolution ON public.stream USING btree (resolution);


-- Name: ix_stream_suggestions_issue_triage_status; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_suggestions_issue_triage_status ON public.stream_suggestions USING btree (issue_triage_status);


-- Name: ix_stream_suggestions_status; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_suggestions_status ON public.stream_suggestions USING btree (status);


-- Name: ix_stream_suggestions_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_suggestions_stream_id ON public.stream_suggestions USING btree (stream_id);


-- Name: ix_stream_suggestions_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_suggestions_updated_at ON public.stream_suggestions USING btree (updated_at);


-- Name: ix_stream_suggestions_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_suggestions_user_id ON public.stream_suggestions USING btree (user_id);


-- Name: ix_stream_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_updated_at ON public.stream USING btree (updated_at);


-- Name: ix_stream_uploader; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_uploader ON public.stream USING btree (uploader);


-- Name: ix_stream_votes_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_votes_stream_id ON public.stream_votes USING btree (stream_id);


-- Name: ix_stream_votes_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_votes_updated_at ON public.stream_votes USING btree (updated_at);


-- Name: ix_stream_votes_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_votes_user_id ON public.stream_votes USING btree (user_id);


-- Name: ix_stream_votes_vote_type; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_stream_votes_vote_type ON public.stream_votes USING btree (vote_type);


-- Name: ix_telegram_stream_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_telegram_stream_stream_id ON public.telegram_stream USING btree (stream_id);


-- Name: ix_telegram_user_forward_telegram_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_telegram_user_forward_telegram_stream_id ON public.telegram_user_forward USING btree (telegram_stream_id);


-- Name: ix_telegram_user_forward_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_telegram_user_forward_user_id ON public.telegram_user_forward USING btree (user_id);


-- Name: ix_torrent_stream_info_hash; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_torrent_stream_info_hash ON public.torrent_stream USING btree (info_hash);


-- Name: ix_torrent_stream_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_torrent_stream_stream_id ON public.torrent_stream USING btree (stream_id);


-- Name: ix_torrent_stream_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_torrent_stream_updated_at ON public.torrent_stream USING btree (updated_at);


-- Name: ix_torrent_tracker_link_tracker_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_torrent_tracker_link_tracker_id ON public.torrent_tracker_link USING btree (tracker_id);


-- Name: ix_tracker_status; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_tracker_status ON public.tracker USING btree (status);


-- Name: ix_tracker_url; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_tracker_url ON public.tracker USING btree (url);


-- Name: ix_tv_metadata_country; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_tv_metadata_country ON public.tv_metadata USING btree (country);


-- Name: ix_tv_metadata_media_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_tv_metadata_media_id ON public.tv_metadata USING btree (media_id);


-- Name: ix_tv_metadata_tv_language; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_tv_metadata_tv_language ON public.tv_metadata USING btree (tv_language);


-- Name: ix_tv_metadata_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_tv_metadata_updated_at ON public.tv_metadata USING btree (updated_at);


-- Name: ix_usenet_stream_indexer; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_usenet_stream_indexer ON public.usenet_stream USING btree (indexer);


-- Name: ix_usenet_stream_nzb_guid; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_usenet_stream_nzb_guid ON public.usenet_stream USING btree (nzb_guid);


-- Name: ix_usenet_stream_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_usenet_stream_stream_id ON public.usenet_stream USING btree (stream_id);


-- Name: ix_user_catalog_is_listed; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_catalog_is_listed ON public.user_catalog USING btree (is_listed);


-- Name: ix_user_catalog_is_public; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_catalog_is_public ON public.user_catalog USING btree (is_public);


-- Name: ix_user_catalog_item_catalog_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_catalog_item_catalog_id ON public.user_catalog_item USING btree (catalog_id);


-- Name: ix_user_catalog_item_display_order; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_catalog_item_display_order ON public.user_catalog_item USING btree (display_order);


-- Name: ix_user_catalog_item_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_catalog_item_media_id ON public.user_catalog_item USING btree (media_id);


-- Name: ix_user_catalog_share_code; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_user_catalog_share_code ON public.user_catalog USING btree (share_code);


-- Name: ix_user_catalog_subscription_catalog_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_catalog_subscription_catalog_id ON public.user_catalog_subscription USING btree (catalog_id);


-- Name: ix_user_catalog_subscription_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_catalog_subscription_user_id ON public.user_catalog_subscription USING btree (user_id);


-- Name: ix_user_catalog_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_catalog_updated_at ON public.user_catalog USING btree (updated_at);


-- Name: ix_user_catalog_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_catalog_user_id ON public.user_catalog USING btree (user_id);


-- Name: ix_user_library_item_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_library_item_media_id ON public.user_library_item USING btree (media_id);


-- Name: ix_user_library_item_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_library_item_user_id ON public.user_library_item USING btree (user_id);


-- Name: ix_user_profiles_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_profiles_updated_at ON public.user_profiles USING btree (updated_at);


-- Name: ix_user_profiles_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_user_profiles_user_id ON public.user_profiles USING btree (user_id);


-- Name: ix_user_profiles_uuid; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_user_profiles_uuid ON public.user_profiles USING btree (uuid);


-- Name: ix_users_contribution_level; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_users_contribution_level ON public.users USING btree (contribution_level);


-- Name: ix_users_contribution_points; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_users_contribution_points ON public.users USING btree (contribution_points);


-- Name: ix_users_email; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_users_email ON public.users USING btree (email);


-- Name: ix_users_is_active; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_users_is_active ON public.users USING btree (is_active);


-- Name: ix_users_role; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_users_role ON public.users USING btree (role);


-- Name: ix_users_telegram_user_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_users_telegram_user_id ON public.users USING btree (telegram_user_id);


-- Name: ix_users_updated_at; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_users_updated_at ON public.users USING btree (updated_at);


-- Name: ix_users_username; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_users_username ON public.users USING btree (username);


-- Name: ix_users_uuid; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_users_uuid ON public.users USING btree (uuid);


-- Name: ix_watch_history_action; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_watch_history_action ON public.watch_history USING btree (action);


-- Name: ix_watch_history_media_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_watch_history_media_id ON public.watch_history USING btree (media_id);


-- Name: ix_watch_history_profile_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_watch_history_profile_id ON public.watch_history USING btree (profile_id);


-- Name: ix_watch_history_source; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_watch_history_source ON public.watch_history USING btree (source);


-- Name: ix_watch_history_user_id; Type: INDEX; Schema: public; Owner: -

CREATE INDEX ix_watch_history_user_id ON public.watch_history USING btree (user_id);


-- Name: ix_youtube_stream_stream_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_youtube_stream_stream_id ON public.youtube_stream USING btree (stream_id);


-- Name: ix_youtube_stream_video_id; Type: INDEX; Schema: public; Owner: -

CREATE UNIQUE INDEX ix_youtube_stream_video_id ON public.youtube_stream USING btree (video_id);


-- Name: acestream_stream acestream_stream_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.acestream_stream
    ADD CONSTRAINT acestream_stream_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: aka_title aka_title_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.aka_title
    ADD CONSTRAINT aka_title_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: annotation_request_dismissal annotation_request_dismissal_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.annotation_request_dismissal
    ADD CONSTRAINT annotation_request_dismissal_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: annotation_request_dismissal annotation_request_dismissal_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.annotation_request_dismissal
    ADD CONSTRAINT annotation_request_dismissal_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: contributions contributions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.contributions
    ADD CONSTRAINT contributions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: episode episode_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode
    ADD CONSTRAINT episode_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


-- Name: episode_image episode_image_episode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode_image
    ADD CONSTRAINT episode_image_episode_id_fkey FOREIGN KEY (episode_id) REFERENCES public.episode(id);


-- Name: episode_image episode_image_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode_image
    ADD CONSTRAINT episode_image_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.metadata_provider(id);


-- Name: episode episode_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode
    ADD CONSTRAINT episode_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.metadata_provider(id);


-- Name: episode episode_season_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode
    ADD CONSTRAINT episode_season_id_fkey FOREIGN KEY (season_id) REFERENCES public.season(id);


-- Name: episode episode_source_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode
    ADD CONSTRAINT episode_source_provider_id_fkey FOREIGN KEY (source_provider_id) REFERENCES public.metadata_provider(id);


-- Name: episode_suggestions episode_suggestions_episode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode_suggestions
    ADD CONSTRAINT episode_suggestions_episode_id_fkey FOREIGN KEY (episode_id) REFERENCES public.episode(id) ON DELETE CASCADE;


-- Name: episode_suggestions episode_suggestions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.episode_suggestions
    ADD CONSTRAINT episode_suggestions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: external_link_stream external_link_stream_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.external_link_stream
    ADD CONSTRAINT external_link_stream_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: file_media_link file_media_link_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.file_media_link
    ADD CONSTRAINT file_media_link_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.stream_file(id) ON DELETE CASCADE;


-- Name: file_media_link file_media_link_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.file_media_link
    ADD CONSTRAINT file_media_link_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: http_stream http_stream_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.http_stream
    ADD CONSTRAINT http_stream_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: iptv_source iptv_source_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.iptv_source
    ADD CONSTRAINT iptv_source_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: media media_blocked_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media
    ADD CONSTRAINT media_blocked_by_user_id_fkey FOREIGN KEY (blocked_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


-- Name: media_cast media_cast_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_cast
    ADD CONSTRAINT media_cast_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media_cast media_cast_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_cast
    ADD CONSTRAINT media_cast_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.person(id) ON DELETE CASCADE;


-- Name: media_catalog_link media_catalog_link_catalog_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_catalog_link
    ADD CONSTRAINT media_catalog_link_catalog_id_fkey FOREIGN KEY (catalog_id) REFERENCES public.catalog(id) ON DELETE CASCADE;


-- Name: media_catalog_link media_catalog_link_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_catalog_link
    ADD CONSTRAINT media_catalog_link_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media media_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media
    ADD CONSTRAINT media_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


-- Name: media_crew media_crew_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_crew
    ADD CONSTRAINT media_crew_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media_crew media_crew_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_crew
    ADD CONSTRAINT media_crew_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.person(id) ON DELETE CASCADE;


-- Name: media_external_id media_external_id_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_external_id
    ADD CONSTRAINT media_external_id_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media_genre_link media_genre_link_genre_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_genre_link
    ADD CONSTRAINT media_genre_link_genre_id_fkey FOREIGN KEY (genre_id) REFERENCES public.genre(id) ON DELETE CASCADE;


-- Name: media_genre_link media_genre_link_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_genre_link
    ADD CONSTRAINT media_genre_link_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media_image media_image_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_image
    ADD CONSTRAINT media_image_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media_image media_image_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_image
    ADD CONSTRAINT media_image_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.metadata_provider(id);


-- Name: media_keyword_link media_keyword_link_keyword_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_keyword_link
    ADD CONSTRAINT media_keyword_link_keyword_id_fkey FOREIGN KEY (keyword_id) REFERENCES public.keyword(id) ON DELETE CASCADE;


-- Name: media_keyword_link media_keyword_link_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_keyword_link
    ADD CONSTRAINT media_keyword_link_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media media_last_refreshed_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media
    ADD CONSTRAINT media_last_refreshed_by_user_id_fkey FOREIGN KEY (last_refreshed_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


-- Name: media media_last_scraped_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media
    ADD CONSTRAINT media_last_scraped_by_user_id_fkey FOREIGN KEY (last_scraped_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


-- Name: media media_migrated_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media
    ADD CONSTRAINT media_migrated_by_user_id_fkey FOREIGN KEY (migrated_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


-- Name: media_parental_certificate_link media_parental_certificate_link_certificate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_parental_certificate_link
    ADD CONSTRAINT media_parental_certificate_link_certificate_id_fkey FOREIGN KEY (certificate_id) REFERENCES public.parental_certificate(id) ON DELETE CASCADE;


-- Name: media_parental_certificate_link media_parental_certificate_link_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_parental_certificate_link
    ADD CONSTRAINT media_parental_certificate_link_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media media_primary_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media
    ADD CONSTRAINT media_primary_provider_id_fkey FOREIGN KEY (primary_provider_id) REFERENCES public.metadata_provider(id);


-- Name: media_production_company_link media_production_company_link_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_production_company_link
    ADD CONSTRAINT media_production_company_link_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.production_company(id) ON DELETE CASCADE;


-- Name: media_production_company_link media_production_company_link_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_production_company_link
    ADD CONSTRAINT media_production_company_link_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media_rating media_rating_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_rating
    ADD CONSTRAINT media_rating_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media_rating media_rating_rating_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_rating
    ADD CONSTRAINT media_rating_rating_provider_id_fkey FOREIGN KEY (rating_provider_id) REFERENCES public.rating_provider(id);


-- Name: media_review media_review_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_review
    ADD CONSTRAINT media_review_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: media_review media_review_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_review
    ADD CONSTRAINT media_review_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.rating_provider(id);


-- Name: media_review media_review_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_review
    ADD CONSTRAINT media_review_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


-- Name: media_trailer media_trailer_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.media_trailer
    ADD CONSTRAINT media_trailer_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: mediafusion_rating mediafusion_rating_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.mediafusion_rating
    ADD CONSTRAINT mediafusion_rating_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: metadata_suggestions metadata_suggestions_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.metadata_suggestions
    ADD CONSTRAINT metadata_suggestions_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: metadata_suggestions metadata_suggestions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.metadata_suggestions
    ADD CONSTRAINT metadata_suggestions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: metadata_votes metadata_votes_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.metadata_votes
    ADD CONSTRAINT metadata_votes_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: metadata_votes metadata_votes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.metadata_votes
    ADD CONSTRAINT metadata_votes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: movie_metadata movie_metadata_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.movie_metadata
    ADD CONSTRAINT movie_metadata_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: person person_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.person
    ADD CONSTRAINT person_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.metadata_provider(id);


-- Name: playback_tracking playback_tracking_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.playback_tracking
    ADD CONSTRAINT playback_tracking_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id);


-- Name: playback_tracking playback_tracking_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.playback_tracking
    ADD CONSTRAINT playback_tracking_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;


-- Name: playback_tracking playback_tracking_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.playback_tracking
    ADD CONSTRAINT playback_tracking_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id);


-- Name: playback_tracking playback_tracking_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.playback_tracking
    ADD CONSTRAINT playback_tracking_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: profile_integration profile_integration_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.profile_integration
    ADD CONSTRAINT profile_integration_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;


-- Name: provider_metadata provider_metadata_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.provider_metadata
    ADD CONSTRAINT provider_metadata_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id);


-- Name: provider_metadata provider_metadata_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.provider_metadata
    ADD CONSTRAINT provider_metadata_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.metadata_provider(id);


-- Name: rss_feed_catalog_pattern rss_feed_catalog_pattern_rss_feed_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.rss_feed_catalog_pattern
    ADD CONSTRAINT rss_feed_catalog_pattern_rss_feed_id_fkey FOREIGN KEY (rss_feed_id) REFERENCES public.rss_feed(id) ON DELETE CASCADE;


-- Name: rss_feed rss_feed_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.rss_feed
    ADD CONSTRAINT rss_feed_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: season season_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.season
    ADD CONSTRAINT season_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.metadata_provider(id);


-- Name: season season_series_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.season
    ADD CONSTRAINT season_series_id_fkey FOREIGN KEY (series_id) REFERENCES public.series_metadata(id);


-- Name: series_metadata series_metadata_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.series_metadata
    ADD CONSTRAINT series_metadata_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: stream_audio_link stream_audio_link_audio_format_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_audio_link
    ADD CONSTRAINT stream_audio_link_audio_format_id_fkey FOREIGN KEY (audio_format_id) REFERENCES public.audio_format(id) ON DELETE CASCADE;


-- Name: stream_audio_link stream_audio_link_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_audio_link
    ADD CONSTRAINT stream_audio_link_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: stream_channel_link stream_channel_link_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_channel_link
    ADD CONSTRAINT stream_channel_link_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.audio_channel(id) ON DELETE CASCADE;


-- Name: stream_channel_link stream_channel_link_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_channel_link
    ADD CONSTRAINT stream_channel_link_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: stream_file stream_file_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_file
    ADD CONSTRAINT stream_file_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: stream_hdr_link stream_hdr_link_hdr_format_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_hdr_link
    ADD CONSTRAINT stream_hdr_link_hdr_format_id_fkey FOREIGN KEY (hdr_format_id) REFERENCES public.hdr_format(id) ON DELETE CASCADE;


-- Name: stream_hdr_link stream_hdr_link_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_hdr_link
    ADD CONSTRAINT stream_hdr_link_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: stream_language_link stream_language_link_language_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_language_link
    ADD CONSTRAINT stream_language_link_language_id_fkey FOREIGN KEY (language_id) REFERENCES public.language(id) ON DELETE CASCADE;


-- Name: stream_language_link stream_language_link_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_language_link
    ADD CONSTRAINT stream_language_link_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: stream_media_link stream_media_link_linked_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_media_link
    ADD CONSTRAINT stream_media_link_linked_by_user_id_fkey FOREIGN KEY (linked_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


-- Name: stream_media_link stream_media_link_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_media_link
    ADD CONSTRAINT stream_media_link_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: stream_media_link stream_media_link_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_media_link
    ADD CONSTRAINT stream_media_link_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: stream_suggestions stream_suggestions_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_suggestions
    ADD CONSTRAINT stream_suggestions_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: stream_suggestions stream_suggestions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_suggestions
    ADD CONSTRAINT stream_suggestions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: stream stream_uploader_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream
    ADD CONSTRAINT stream_uploader_user_id_fkey FOREIGN KEY (uploader_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


-- Name: stream_votes stream_votes_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_votes
    ADD CONSTRAINT stream_votes_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: stream_votes stream_votes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.stream_votes
    ADD CONSTRAINT stream_votes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: telegram_stream telegram_stream_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.telegram_stream
    ADD CONSTRAINT telegram_stream_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: telegram_user_forward telegram_user_forward_telegram_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.telegram_user_forward
    ADD CONSTRAINT telegram_user_forward_telegram_stream_id_fkey FOREIGN KEY (telegram_stream_id) REFERENCES public.telegram_stream(id) ON DELETE CASCADE;


-- Name: telegram_user_forward telegram_user_forward_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.telegram_user_forward
    ADD CONSTRAINT telegram_user_forward_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: torrent_stream torrent_stream_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.torrent_stream
    ADD CONSTRAINT torrent_stream_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: torrent_tracker_link torrent_tracker_link_torrent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.torrent_tracker_link
    ADD CONSTRAINT torrent_tracker_link_torrent_id_fkey FOREIGN KEY (torrent_id) REFERENCES public.torrent_stream(id) ON DELETE CASCADE;


-- Name: torrent_tracker_link torrent_tracker_link_tracker_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.torrent_tracker_link
    ADD CONSTRAINT torrent_tracker_link_tracker_id_fkey FOREIGN KEY (tracker_id) REFERENCES public.tracker(id) ON DELETE CASCADE;


-- Name: tv_metadata tv_metadata_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.tv_metadata
    ADD CONSTRAINT tv_metadata_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: usenet_stream usenet_stream_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.usenet_stream
    ADD CONSTRAINT usenet_stream_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- Name: user_catalog_item user_catalog_item_catalog_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog_item
    ADD CONSTRAINT user_catalog_item_catalog_id_fkey FOREIGN KEY (catalog_id) REFERENCES public.user_catalog(id) ON DELETE CASCADE;


-- Name: user_catalog_item user_catalog_item_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog_item
    ADD CONSTRAINT user_catalog_item_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: user_catalog_subscription user_catalog_subscription_catalog_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog_subscription
    ADD CONSTRAINT user_catalog_subscription_catalog_id_fkey FOREIGN KEY (catalog_id) REFERENCES public.user_catalog(id) ON DELETE CASCADE;


-- Name: user_catalog_subscription user_catalog_subscription_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog_subscription
    ADD CONSTRAINT user_catalog_subscription_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: user_catalog user_catalog_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_catalog
    ADD CONSTRAINT user_catalog_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: user_library_item user_library_item_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_library_item
    ADD CONSTRAINT user_library_item_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


-- Name: user_library_item user_library_item_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_library_item
    ADD CONSTRAINT user_library_item_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: user_profiles user_profiles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: watch_history watch_history_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.watch_history
    ADD CONSTRAINT watch_history_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id);


-- Name: watch_history watch_history_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.watch_history
    ADD CONSTRAINT watch_history_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;


-- Name: watch_history watch_history_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.watch_history
    ADD CONSTRAINT watch_history_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Name: youtube_stream youtube_stream_stream_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -

ALTER TABLE ONLY public.youtube_stream
    ADD CONSTRAINT youtube_stream_stream_id_fkey FOREIGN KEY (stream_id) REFERENCES public.stream(id) ON DELETE CASCADE;


-- PostgreSQL database dump complete


