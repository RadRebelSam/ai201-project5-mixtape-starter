# AI Usage

I used AI to speed up codebase orientation by summarizing the responsibilities of the main service modules and tracing the route-to-service call chains. I also used it to help frame likely bug patterns from the README descriptions, then verified each diagnosis by reading the code directly and reproducing the behavior in tests or a small local probe. When the AI explanation was incomplete, I checked the actual code path myself before changing anything.

# Codebase Map

- [app.py](app.py) creates the Flask app, configures SQLAlchemy, and registers the four blueprints.
- [models.py](models.py) defines the database schema: users, songs, playlists, listening events, ratings, tags, and notifications.
- [routes/songs.py](routes/songs.py) handles song search, song detail, rating, and listen actions. It mostly parses request data and delegates to services.
- [routes/playlists.py](routes/playlists.py) creates playlists, adds songs to playlists, and returns playlist contents.
- [routes/users.py](routes/users.py) exposes user lookup, streak lookup, and notification endpoints.
- [routes/feed.py](routes/feed.py) serves the listening-now and activity feeds.
- [services/streak_service.py](services/streak_service.py) updates and reads listening streak state.
- [services/feed_service.py](services/feed_service.py) builds the friends-listening feed from recent listening events.
- [services/search_service.py](services/search_service.py) searches songs by title or artist and formats results.
- [services/notification_service.py](services/notification_service.py) creates and reads notifications, and handles playlist-add and rating notifications.
- [services/playlist_service.py](services/playlist_service.py) creates playlists and returns ordered playlist songs.
- [seed_data.py](seed_data.py) resets and seeds the database with example users, songs, playlists, listening events, and notifications.

Data flow example: when a user rates a song, `POST /songs/<song_id>/rate` in [routes/songs.py](routes/songs.py) calls `rate_song()` in [services/notification_service.py](services/notification_service.py). That service validates the score, creates or updates the `Rating`, commits it, and then creates a notification for the original sharer if the rater is a different user.

Patterns I noticed: the routes are thin and mostly do validation/JSON formatting, while the services hold the business rules and database writes. The app also uses helper services for the same resource repeatedly, so bugs tend to live in one service function rather than in the route layer.

# Root Cause Analyses

## Issue 1 - My listening streak keeps resetting

Reproduced by listening on Saturday and then again on Sunday in the existing streak test. The streak stayed at 1 instead of incrementing to 2.

I traced the flow from `POST /songs/<song_id>/listen` in [routes/songs.py](routes/songs.py) into `record_listening_event()` and then into `update_listening_streak()` in [services/streak_service.py](services/streak_service.py). The suspicious line was the extra weekday check inside the consecutive-day branch, and the Sunday-specific regression test confirmed it was the exact condition blocking the increment.

The root cause was that the code treated Sunday as a special reset case even when the previous listen was exactly one day earlier. `datetime.date().weekday()` returns `6` for Sunday, so `today.weekday() != 6` prevented the streak from incrementing on the one day it should have.

The fix was to remove the Sunday exception and increment the streak whenever `days_since_last == 1`. I rechecked the nearby behavior to make sure same-day listens still do nothing and gaps of more than one day still reset the streak to 1. The existing streak tests, including the Sunday case, now pass.

## Issue 2 - Friends Listening Now shows people from yesterday

I reproduced this with a controlled time probe: I fixed "now" to 2024-06-17 01:00 UTC, created one listening event at 2024-06-16 23:30 UTC and another at 2024-06-17 00:30 UTC, and called `get_friends_listening_now()`. The old logic included the previous-calendar-day event because it only checked whether the listen was within the last 24 hours.

I followed the route from [routes/feed.py](routes/feed.py) into `get_friends_listening_now()` in [services/feed_service.py](services/feed_service.py). The key clue was the `RECENT_THRESHOLD = timedelta(hours=24)` constant: that is a rolling window, not a calendar-day boundary. The new regression test uses a monkeypatched clock to make the bug deterministic.

The root cause was a mismatch between the feature name and the implementation. "Listening now" was supposed to mean current-day activity, but the code used a rolling 24-hour cutoff. That let late-night listens from yesterday appear at 1 AM today.

The fix changed the cutoff to the start of the current UTC day and kept the existing friend deduplication logic intact. I checked that same-day events still appear and that the previous-day event is filtered out. The new feed regression test passes.

## Issue 3 - The same song keeps showing up twice in search

I reproduced the underlying duplication risk by looking at the old search query shape against the seeded multi-tag song data. The query joined `song_tags` even though the search only filters title and artist, so a song with multiple tags produces multiple joined rows for the same song.

I traced the route from [routes/songs.py](routes/songs.py) into `search_songs()` in [services/search_service.py](services/search_service.py). The unnecessary `outerjoin(song_tags, ...)` was the suspicious piece because the service never reads tag columns from the join. Removing the join left the search logic unchanged but removed the duplication source.

The root cause was an unneeded many-to-many join in a query that only needed the `song` table. On songs with more than one tag, the join could multiply the SQL result rows, which is what caused duplicate songs to surface in search results in the buggy implementation.

The fix was to query `Song` directly by title or artist and let the `tags` relationship load separately when `to_dict()` is called. I re-ran the search tests for songs with zero, one, and multiple tags to confirm the results still come back once each.

## Issue 4 - I got notified when a friend added my song to a playlist but not when they rated it

I reproduced this by rating a shared song from another user and then checking the original sharer's notifications. Before the fix, the rating was saved but the notification count did not change.

I followed the route from [routes/songs.py](routes/songs.py) into `rate_song()` in [services/notification_service.py](services/notification_service.py). The playlist-add path already called `create_notification()` after its database update, but the rating path stopped after committing the `Rating` row. The asymmetry was the clue: one code path had the notification step, the other didn't.

The root cause was that the rating workflow never created a notification at all. The service updated the rating record and returned it, but it did not mirror the existing playlist-add pattern for alerting the song's original sharer.

The fix was to create a `song_rated` notification after the rating commit when the rater is not also the song sharer. I verified that the new regression test sees exactly one notification with the expected body, and that self-ratings do not generate a notification.

## Issue 5 - The last song in a playlist never shows up

I reproduced this with the playlist tests: a five-song playlist returned only four songs, and the missing item was always the final track in position order.

I traced the route from [routes/playlists.py](routes/playlists.py) into `get_playlist_songs()` in [services/playlist_service.py](services/playlist_service.py). The function queried the songs in the correct order, then immediately sliced the result with `songs[:-1]`. That made the last song disappear every time, regardless of playlist size.

The root cause was a stray slice that dropped the final element of the result list. There was no business rule behind it; it was simply truncating the data after the query already returned the right songs in the right order.

The fix was to return the full ordered list unchanged. I rechecked the empty-playlist case and the ordered five-song case to confirm the slice removal did not break either boundary.