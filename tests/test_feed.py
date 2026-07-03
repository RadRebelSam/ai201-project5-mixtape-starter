"""
tests/test_feed.py — Mixtape

Tests for feed logic.
"""

from datetime import datetime, timezone

import pytest

import services.feed_service as feed_service
from app import create_app, db
from models import ListeningEvent, Song, User, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def feed_seed(app):
    with app.app_context():
        listener = User(username="listener", email="listener@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([listener, friend])
        db.session.flush()

        db.session.execute(friendships.insert().values(user_id=listener.id, friend_id=friend.id))
        db.session.execute(friendships.insert().values(user_id=friend.id, friend_id=listener.id))

        song = Song(title="Night Drive", artist="Test Artist", shared_by=friend.id)
        db.session.add(song)
        db.session.flush()

        db.session.commit()

        return {
            "listener_id": listener.id,
            "friend_id": friend.id,
            "song_id": song.id,
        }


def test_listening_now_excludes_previous_calendar_day(app, feed_seed, monkeypatch):
    with app.app_context():
        fixed_now = datetime(2024, 6, 17, 1, 0, 0, tzinfo=timezone.utc)

        class FixedDateTime:
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        monkeypatch.setattr(feed_service, "datetime", FixedDateTime)

        db.session.add(
            ListeningEvent(
                user_id=feed_seed["friend_id"],
                song_id=feed_seed["song_id"],
                listened_at=datetime(2024, 6, 16, 23, 30, 0, tzinfo=timezone.utc),
            )
        )
        db.session.add(
            ListeningEvent(
                user_id=feed_seed["friend_id"],
                song_id=feed_seed["song_id"],
                listened_at=datetime(2024, 6, 17, 0, 30, 0, tzinfo=timezone.utc),
            )
        )
        db.session.commit()

        feed = get_friends_listening_now(feed_seed["listener_id"])

        assert len(feed) == 1
        assert feed[0]["listened_at"].startswith("2024-06-17T00:30:00")