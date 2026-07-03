"""
tests/test_notifications.py — Mixtape

Tests for notification logic.
"""

import pytest

from app import create_app, db
from models import Song, User
from services.notification_service import get_notifications, rate_song


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def test_rating_a_shared_song_creates_notification(app):
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Rated Track", artist="Test Artist", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        rate_song(rater.id, song.id, 5)

        notifications = get_notifications(sharer.id)
        assert len(notifications) == 1
        assert notifications[0]["type"] == "song_rated"
        assert "rated your song 'Rated Track' 5/5" in notifications[0]["body"]