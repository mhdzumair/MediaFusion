import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from reference.routers.content import contributions, stream_suggestions
from db.enums import ContributionStatus, UserRole
from db.models import Contribution, ContributionSettings, User


class _QueryResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def all(self):
        return self._value

    def one(self):
        return self._value


class _FakeSession:
    def __init__(self, users: dict[int, User] | None = None, exec_results: list | None = None):
        self.users = users or {}
        self.exec_results = list(exec_results or [])
        self.added = []
        self.commit_calls = 0
        self.refresh_calls = 0

    async def get(self, model, key):
        if model is User:
            return self.users.get(key)
        return None

    async def exec(self, _query):
        if not self.exec_results:
            raise AssertionError("No queued exec result available for this test session.")
        return _QueryResult(self.exec_results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, _obj):
        self.refresh_calls += 1

    async def flush(self):
        return None


def _settings(points_per_stream_edit: int = 3) -> ContributionSettings:
    return ContributionSettings(
        id="default",
        points_per_stream_edit=points_per_stream_edit,
        contributor_threshold=10,
        trusted_threshold=50,
        expert_threshold=200,
    )


def _make_user(
    user_id: int,
    *,
    role: UserRole = UserRole.USER,
    is_active: bool = True,
    points: int = 0,
) -> User:
    return User(
        id=user_id,
        email=f"user{user_id}@example.com",
        username=f"user{user_id}",
        role=role,
        is_active=is_active,
        contribution_points=points,
        stream_edits_approved=0,
        contribution_level="new",
    )


def _make_stream_suggestion(suggestion_id: str = "sug-1"):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=suggestion_id,
        user_id=1,
        stream_id=7,
        suggestion_type="field_correction:name",
        current_value="Old Name",
        suggested_value="New Name",
        reason="Fix typo",
        status=stream_suggestions.STATUS_PENDING,
        created_at=now,
        reviewed_by=None,
        reviewed_at=None,
        review_notes=None,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_moderator_approval_awards_points_for_pending_import(monkeypatch):
    contributor = _make_user(1, points=2)
    reviewer = _make_user(2, role=UserRole.MODERATOR)
    session = _FakeSession(users={1: contributor})
    contribution = Contribution(
        user_id=1,
        contribution_type="torrent",
        target_id="tt1234567",
        data={},
        status=ContributionStatus.PENDING,
    )

    async def fake_settings(_session):
        return _settings(points_per_stream_edit=3)

    async def fake_process(_session, _data, _contributor):
        return {"status": "success", "stream_id": 42}

    monkeypatch.setattr(contributions, "get_contribution_settings", fake_settings)
    monkeypatch.setattr(contributions, "get_import_processor", lambda _ctype: fake_process)

    await contributions._apply_contribution_review(
        session,
        contribution,
        ContributionStatus.APPROVED,
        reviewer,
        "looks good",
        logging.getLogger(__name__),
    )

    assert contribution.status == ContributionStatus.APPROVED
    assert contributor.contribution_points == 5
    assert contributor.stream_edits_approved == 1


@pytest.mark.asyncio
async def test_auto_approved_import_creation_awards_points(monkeypatch):
    user = _make_user(10, role=UserRole.USER, is_active=True, points=0)
    session = _FakeSession(users={10: user})
    payload = contributions.ContributionCreate(
        contribution_type="torrent",
        target_id="tt9876543",
        data={"is_anonymous": False},
    )

    async def fake_settings(_session):
        return _settings(points_per_stream_edit=4)

    async def fake_media_id(_session, _contribution):
        return None

    monkeypatch.setattr(contributions, "get_contribution_settings", fake_settings)
    monkeypatch.setattr(contributions, "resolve_contribution_media_id", fake_media_id)

    response = await contributions.create_contribution(payload, user, session)

    assert response.status == ContributionStatus.APPROVED.value
    assert user.contribution_points == 4
    assert user.stream_edits_approved == 1


@pytest.mark.asyncio
async def test_anonymous_auto_approved_import_does_not_award_points(monkeypatch):
    admin = _make_user(11, role=UserRole.ADMIN, is_active=True, points=7)
    session = _FakeSession(users={11: admin})
    payload = contributions.ContributionCreate(
        contribution_type="torrent",
        target_id="tt1111111",
        data={"is_anonymous": True},
    )

    async def fake_settings(_session):
        return _settings(points_per_stream_edit=5)

    async def fake_media_id(_session, _contribution):
        return None

    monkeypatch.setattr(contributions, "get_contribution_settings", fake_settings)
    monkeypatch.setattr(contributions, "resolve_contribution_media_id", fake_media_id)

    response = await contributions.create_contribution(payload, admin, session)

    assert response.status == ContributionStatus.APPROVED.value
    assert response.user_id is None
    assert admin.contribution_points == 7
    assert admin.stream_edits_approved == 0


@pytest.mark.asyncio
async def test_single_stream_review_awards_points_even_when_apply_fails(monkeypatch):
    suggestion = _make_stream_suggestion("single-1")
    stream = SimpleNamespace(id=7, name="Example Stream", media_links=[])
    author = _make_user(1, points=0)
    moderator = _make_user(2, role=UserRole.MODERATOR)
    moderator.username = "mod"
    session = _FakeSession(exec_results=[suggestion, stream, author, "author"])

    async def fake_settings(_session):
        return _settings(points_per_stream_edit=3)

    async def fake_apply(*_args, **_kwargs):
        return False

    monkeypatch.setattr(stream_suggestions, "get_contribution_settings", fake_settings)
    monkeypatch.setattr(stream_suggestions, "apply_stream_changes", fake_apply)

    request = stream_suggestions.StreamSuggestionReviewRequest(action="approve", review_notes="approved")
    response = await stream_suggestions.review_stream_suggestion(
        suggestion_id=suggestion.id,
        request=request,
        current_user=moderator,
        session=session,
    )

    assert response.status == stream_suggestions.STATUS_APPROVED
    assert author.contribution_points == 3
    assert author.stream_edits_approved == 1


@pytest.mark.asyncio
async def test_bulk_stream_review_awards_points_even_when_apply_fails(monkeypatch):
    suggestion = _make_stream_suggestion("bulk-1")
    stream = SimpleNamespace(id=7, name="Example Stream", media_links=[])
    author = _make_user(1, points=0)
    moderator = _make_user(2, role=UserRole.MODERATOR)
    session = _FakeSession(exec_results=[suggestion, stream, author])

    async def fake_settings(_session):
        return _settings(points_per_stream_edit=2)

    async def fake_apply(*_args, **_kwargs):
        return False

    monkeypatch.setattr(stream_suggestions, "get_contribution_settings", fake_settings)
    monkeypatch.setattr(stream_suggestions, "apply_stream_changes", fake_apply)

    result = await stream_suggestions.bulk_review_stream_suggestions(
        suggestion_ids=[suggestion.id],
        action="approve",
        review_notes="approved",
        current_user=moderator,
        session=session,
    )

    assert result["approved"] == 1
    assert author.contribution_points == 2
    assert author.stream_edits_approved == 1
